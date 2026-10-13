"""Freeze exact command inputs from an immutable local Git commit.

The CLI never derives its source name-set or bytes from the mutable worktree.
It reads selected blobs from one exact commit, stages those exact bytes in a
private directory for the existing parser, and then checks the compiled packet
against a manifest that binds commit, root tree, paths, modes, object IDs,
sizes, and SHA-256 digests.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import validate_commands as ticket_contract
from command_batch_model import (
    BatchError,
    FORBIDDEN_SEPARATORS,
    MAX_INPUT_BYTES,
    RECEIPT_RESERVED_NAMES,
    _canonical_json_bytes,
    _sha256,
)


GIT_SHA1_RE = re.compile(r"[0-9a-f]{40}\Z")
MAX_TREE_LIST_BYTES = 2 * 1024 * 1024
MAX_SELECTED_SOURCES = 4096
GIT_TIMEOUT_SECONDS = 15
_REGULAR_BLOB_MODES = frozenset({"100644", "100755"})


@dataclass(frozen=True)
class _GitEntry:
    role: str
    git_path: str
    name: str
    mode: str
    object_id: str
    data: bytes
    sha256: str

    @property
    def byte_count(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class FrozenGitSnapshot:
    """Private staged view plus the exact immutable Git binding it came from."""

    repository: Path
    commit_sha: str
    root_tree_sha: str
    manifest_sha256: str
    source_ref: str
    ticket_root: Path
    receipt_root: Path
    ticket_paths: tuple[Path, ...]
    receipt_paths: tuple[Path, ...]
    expected_source_set: tuple[dict[str, Any], ...]

    def assert_packet_sources(self, packet: dict[str, Any]) -> None:
        """Reject any staging splice, omission, replacement, or byte drift."""
        try:
            payload = packet["payload"]
            observed_ref = payload["source_ref"]
            observed_set = payload["source_set"]
        except (KeyError, TypeError) as exc:
            raise BatchError("GIT_PACKET_SHAPE: packet lacks source binding fields") from exc
        if observed_ref != self.source_ref:
            raise BatchError(
                f"GIT_SOURCE_REF_MISMATCH: {observed_ref!r} != {self.source_ref!r}"
            )
        expected = [dict(item) for item in self.expected_source_set]
        if observed_set != expected:
            raise BatchError(
                "GIT_SOURCE_SET_MISMATCH: compiled paths/bytes differ from the exact Git manifest"
            )


def _git_environment() -> dict[str, str]:
    """Return a deterministic environment that cannot redirect repository reads."""
    env = os.environ.copy()
    for key in tuple(env):
        if key in {
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CEILING_DIRECTORIES",
            "GIT_COMMON_DIR",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_PARAMETERS",
            "GIT_DIR",
            "GIT_DISCOVERY_ACROSS_FILESYSTEM",
            "GIT_INDEX_FILE",
            "GIT_NAMESPACE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_REPLACE_REF_BASE",
            "GIT_WORK_TREE",
        } or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            env.pop(key, None)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    return env


def _run_git(
    repository: Path,
    *arguments: str,
    max_stdout: int = MAX_TREE_LIST_BYTES,
) -> bytes:
    command = [
        "git",
        "--no-replace-objects",
        "-C",
        os.fspath(repository),
        *arguments,
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
            env=_git_environment(),
        )
    except FileNotFoundError as exc:
        raise BatchError("GIT_UNAVAILABLE: git executable was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise BatchError(f"GIT_TIMEOUT: {' '.join(arguments[:2])}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 500:
            detail = detail[:497] + "..."
        raise BatchError(
            f"GIT_COMMAND_FAILED: {' '.join(arguments[:2])}: {detail or 'nonzero exit'}"
        )
    if len(completed.stdout) > max_stdout:
        raise BatchError(
            f"GIT_OUTPUT_OVERSIZE: {' '.join(arguments[:2])}: exceeds {max_stdout} bytes"
        )
    return completed.stdout


def _normalize_repository(repository: Path) -> Path:
    try:
        normalized = Path(repository).resolve(strict=True)
    except OSError as exc:
        raise BatchError(f"GIT_REPOSITORY: cannot resolve {repository}: {exc}") from exc
    if not normalized.is_dir():
        raise BatchError(f"GIT_REPOSITORY: {normalized} is not a directory")
    inside = _run_git(normalized, "rev-parse", "--is-inside-work-tree", max_stdout=64)
    if inside != b"true\n":
        raise BatchError(f"GIT_REPOSITORY: {normalized} is not a worktree")
    return normalized


def _normalize_tree_prefix(repository: Path, value: Path, *, label: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        # On Windows, tempfile paths can arrive through an 8.3 alias while
        # repository.resolve() returns the long spelling. Canonicalize without
        # requiring the mutable leaf to exist; Git, not the worktree, remains
        # the source of bytes and names.
        absolute = candidate.resolve(strict=False)
        try:
            relative = absolute.relative_to(repository)
        except ValueError as exc:
            raise BatchError(f"GIT_{label}: path must be inside the repository") from exc
    else:
        relative = candidate
    pure = PurePosixPath(relative.as_posix())
    if pure.is_absolute() or not pure.parts:
        raise BatchError(f"GIT_{label}: expected a repository-relative directory")
    for part in pure.parts:
        if part in {"", ".", ".."} or ":" in part or "\\" in part:
            raise BatchError(f"GIT_{label}: unsafe path component {part!r}")
        for char in part:
            if char in FORBIDDEN_SEPARATORS or unicodedata.category(char) == "Cc":
                raise BatchError(f"GIT_{label}: control/separator in path")
    return "/".join(pure.parts)


def _safe_git_name(raw: bytes, *, tree_prefix: str) -> str:
    try:
        name = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BatchError(f"GIT_PATH_UTF8: {tree_prefix}: invalid UTF-8 name") from exc
    if name in {"", ".", ".."} or "/" in name or "\\" in name:
        raise BatchError(f"GIT_PATH: {tree_prefix}: unsafe immediate name {name!r}")
    for char in name:
        if char in FORBIDDEN_SEPARATORS or unicodedata.category(char) == "Cc":
            raise BatchError(f"GIT_PATH_CONTROL: {tree_prefix}/{name}")
    return name


def _verify_blob_object(object_id: str, data: bytes) -> None:
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    observed = hashlib.sha1(header + data).hexdigest()
    if observed != object_id:
        raise BatchError(f"GIT_OBJECT_DIGEST: {object_id} != recomputed {observed}")


def _selected_entries(
    repository: Path,
    commit_sha: str,
    tree_prefix: str,
    *,
    role: str,
    reserved_names: frozenset[str],
) -> list[_GitEntry]:
    listing = _run_git(
        repository,
        "ls-tree",
        "-z",
        "-l",
        f"{commit_sha}:{tree_prefix}",
        max_stdout=MAX_TREE_LIST_BYTES,
    )
    result: list[_GitEntry] = []
    seen_names: set[str] = set()
    for record in listing.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_name = record.split(b"\t", 1)
        except ValueError as exc:
            raise BatchError(f"GIT_TREE_RECORD: malformed entry under {tree_prefix}") from exc
        parts = metadata.split()
        if len(parts) != 4:
            raise BatchError(f"GIT_TREE_RECORD: malformed metadata under {tree_prefix}")
        raw_mode, raw_type, raw_object_id, raw_size = parts
        name = _safe_git_name(raw_name, tree_prefix=tree_prefix)
        if not name.endswith(".txt") or name in reserved_names:
            continue
        if name in seen_names:
            raise BatchError(f"GIT_DUPLICATE_PATH: {tree_prefix}/{name}")
        seen_names.add(name)
        mode = raw_mode.decode("ascii", errors="strict")
        object_type = raw_type.decode("ascii", errors="strict")
        object_id = raw_object_id.decode("ascii", errors="strict")
        if object_type != "blob" or mode not in _REGULAR_BLOB_MODES:
            raise BatchError(
                f"GIT_INPUT_NOT_REGULAR: {tree_prefix}/{name}: {mode} {object_type}"
            )
        if not GIT_SHA1_RE.fullmatch(object_id):
            raise BatchError(f"GIT_OBJECT_ID: invalid SHA-1 for {tree_prefix}/{name}")
        if raw_size == b"BAD":
            # ls-tree -l prints BAD for an object it cannot read (for example a
            # promised blob that GIT_NO_LAZY_FETCH prevents materializing).
            raise BatchError(
                f"GIT_COMMAND_FAILED: ls-tree -l: object unavailable for {tree_prefix}/{name}"
            )
        try:
            byte_count = int(raw_size)
        except ValueError as exc:
            raise BatchError(f"GIT_OBJECT_SIZE: invalid size for {tree_prefix}/{name}") from exc
        if byte_count < 0 or byte_count > MAX_INPUT_BYTES:
            raise BatchError(
                f"GIT_INPUT_OVERSIZE: {tree_prefix}/{name}: exceeds {MAX_INPUT_BYTES} bytes"
            )
        data = _run_git(
            repository,
            "cat-file",
            "blob",
            object_id,
            max_stdout=byte_count + 1,
        )
        if len(data) != byte_count:
            raise BatchError(
                f"GIT_OBJECT_SIZE: {tree_prefix}/{name}: tree says {byte_count}, blob has {len(data)}"
            )
        _verify_blob_object(object_id, data)
        result.append(
            _GitEntry(
                role=role,
                git_path=f"{tree_prefix}/{name}",
                name=name,
                mode=mode,
                object_id=object_id,
                data=data,
                sha256=_sha256(data),
            )
        )
        if len(result) > MAX_SELECTED_SOURCES:
            raise BatchError(
                f"GIT_SOURCE_COUNT: more than {MAX_SELECTED_SOURCES} selected {role} files"
            )
    result.sort(key=lambda item: item.git_path)
    return result


def _write_stage_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BatchError(f"GIT_STAGE_CREATE: {path.name}: {exc}") from exc
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BatchError(f"GIT_STAGE_WRITE: {path.name}: zero-byte write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _build_snapshot(
    repository: Path,
    commit_sha: str,
    commands_dir: Path,
    receipts_dir: Path,
    stage_root: Path,
) -> FrozenGitSnapshot:
    repository = _normalize_repository(repository)
    if not GIT_SHA1_RE.fullmatch(commit_sha):
        raise BatchError("GIT_COMMIT: source-ref must be one exact lowercase 40-character SHA-1")
    object_type = _run_git(repository, "cat-file", "-t", commit_sha, max_stdout=32)
    if object_type != b"commit\n":
        raise BatchError(f"GIT_COMMIT: {commit_sha} is not a commit object")
    resolved = _run_git(
        repository,
        "rev-parse",
        "--verify",
        f"{commit_sha}^{{commit}}",
        max_stdout=128,
    ).decode("ascii").strip()
    if resolved != commit_sha:
        raise BatchError(f"GIT_COMMIT: {commit_sha} did not resolve exactly")
    root_tree_sha = _run_git(
        repository,
        "rev-parse",
        f"{commit_sha}^{{tree}}",
        max_stdout=128,
    ).decode("ascii").strip()
    if not GIT_SHA1_RE.fullmatch(root_tree_sha):
        raise BatchError(f"GIT_ROOT_TREE: invalid tree object for {commit_sha}")

    commands_prefix = _normalize_tree_prefix(repository, commands_dir, label="COMMANDS_DIR")
    receipts_prefix = _normalize_tree_prefix(repository, receipts_dir, label="RECEIPTS_DIR")
    tickets = _selected_entries(
        repository,
        commit_sha,
        commands_prefix,
        role="ticket",
        reserved_names=ticket_contract.RESERVED_NAMES,
    )
    receipts = _selected_entries(
        repository,
        commit_sha,
        receipts_prefix,
        role="receipt",
        reserved_names=RECEIPT_RESERVED_NAMES,
    )

    manifest = [
        {
            "byte_count": entry.byte_count,
            "git_path": entry.git_path,
            "mode": entry.mode,
            "object_id": entry.object_id,
            "role": entry.role,
            "sha256": entry.sha256,
        }
        for entry in tickets + receipts
    ]
    manifest.sort(key=lambda item: (item["role"], item["git_path"]))
    manifest_sha256 = _sha256(_canonical_json_bytes(manifest))
    source_ref = (
        f"git-sha1:{commit_sha}:tree:{root_tree_sha}:manifest:{manifest_sha256}"
    )

    ticket_root = stage_root / "tickets"
    receipt_root = stage_root / "receipts"
    ticket_root.mkdir(mode=0o700)
    receipt_root.mkdir(mode=0o700)
    ticket_paths: list[Path] = []
    receipt_paths: list[Path] = []
    expected_source_set: list[dict[str, Any]] = []
    for entry in tickets:
        path = ticket_root / entry.name
        _write_stage_file(path, entry.data)
        ticket_paths.append(path)
        expected_source_set.append(
            {
                "byte_count": entry.byte_count,
                "path": f"COMMANDS/{entry.name}",
                "role": "ticket",
                "sha256": entry.sha256,
            }
        )
    for entry in receipts:
        path = receipt_root / entry.name
        _write_stage_file(path, entry.data)
        receipt_paths.append(path)
        expected_source_set.append(
            {
                "byte_count": entry.byte_count,
                "path": f"COMMANDS/RECEIPTS/{entry.name}",
                "role": "receipt",
                "sha256": entry.sha256,
            }
        )
    expected_source_set.sort(key=lambda item: (item["role"], item["path"]))
    return FrozenGitSnapshot(
        repository=repository,
        commit_sha=commit_sha,
        root_tree_sha=root_tree_sha,
        manifest_sha256=manifest_sha256,
        source_ref=source_ref,
        ticket_root=ticket_root,
        receipt_root=receipt_root,
        ticket_paths=tuple(ticket_paths),
        receipt_paths=tuple(receipt_paths),
        expected_source_set=tuple(expected_source_set),
    )


@contextmanager
def frozen_git_snapshot(
    repository: Path,
    commit_sha: str,
    commands_dir: Path = Path("COMMANDS"),
    receipts_dir: Path = Path("COMMANDS/RECEIPTS"),
) -> Iterator[FrozenGitSnapshot]:
    """Yield exact selected sources from one immutable local Git commit."""
    with tempfile.TemporaryDirectory(prefix="command-batch-git-") as temporary:
        stage_root = Path(temporary)
        snapshot = _build_snapshot(
            Path(repository),
            commit_sha,
            Path(commands_dir),
            Path(receipts_dir),
            stage_root,
        )
        yield snapshot
