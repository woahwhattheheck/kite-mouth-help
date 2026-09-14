"""Bounded source reads, receipt parsing, directory freezing, and safe outputs."""
from __future__ import annotations
import os
import stat
from pathlib import Path
import validate_commands as ticket_contract
from command_batch_model import (
    ALLOWED_RECEIPT_STATEMENTS, BatchError, FORBIDDEN_SEPARATORS,
    MAX_INPUT_BYTES, RECEIPT_KEY_RE, RECEIPT_RESERVED_NAMES, SHA256_RE,
    Receipt, SourceBytes, _decode_text, _errno_name, _sha256,
    _stable_generation, _stable_identity,
)


def read_bounded_regular(
    path: Path,
    *,
    label: str,
    role: str,
    max_bytes: int = MAX_INPUT_BYTES,
) -> SourceBytes:
    """Read one exact ordinary-file generation without following its final symlink."""
    path = Path(path)
    try:
        before_path = path.lstat()
    except OSError as exc:
        raise BatchError(f"INPUT_STAT_ERROR: {label}: {_errno_name(exc)}") from exc
    if stat.S_ISLNK(before_path.st_mode):
        raise BatchError(f"INPUT_SYMLINK: {label}: final component must not be a symlink")
    if not stat.S_ISREG(before_path.st_mode):
        raise BatchError(f"INPUT_NOT_REGULAR: {label}: input must be a regular file")
    if before_path.st_size > max_bytes:
        raise BatchError(f"INPUT_OVERSIZE: {label}: exceeds {max_bytes} bytes")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BatchError(f"INPUT_OPEN_ERROR: {label}: {_errno_name(exc)}") from exc

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise BatchError(f"INPUT_NOT_REGULAR: {label}: opened input must be a regular file")
        if _stable_identity(before_path) != _stable_identity(opened):
            raise BatchError(f"INPUT_REPLACED: {label}: path changed before open")
        if opened.st_size > max_bytes:
            raise BatchError(f"INPUT_OVERSIZE: {label}: exceeds {max_bytes} bytes")

        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(16 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise BatchError(f"INPUT_OVERSIZE: {label}: exceeds {max_bytes} bytes")

        after_fd = os.fstat(fd)
        if _stable_generation(opened) != _stable_generation(after_fd):
            raise BatchError(f"INPUT_CHANGED: {label}: file changed while being read")
        try:
            after_path = path.lstat()
        except OSError as exc:
            raise BatchError(f"INPUT_REPLACED: {label}: path disappeared after read") from exc
        if _stable_identity(after_path) != _stable_identity(after_fd):
            raise BatchError(f"INPUT_REPLACED: {label}: path changed after read")
    finally:
        os.close(fd)

    return SourceBytes(role=role, path=path, label=label, data=data, sha256=_sha256(data))


def parse_receipt_bytes(data: bytes, *, path: Path, label: str) -> Receipt:
    """Parse the repository's line-oriented receipt evidence without trusting it."""
    text = _decode_text(data, label=label, noun="RECEIPT")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != "RECEIPT":
        raise BatchError(f"RECEIPT_HEADER: {label}: first line must be exactly RECEIPT")

    fields: dict[str, str] = {}
    statements: list[str] = []
    for lineno, raw_line in enumerate(lines[1:], 2):
        stripped = raw_line.strip(" ")
        if not stripped:
            continue
        if "=" not in raw_line:
            if raw_line != stripped or stripped not in ALLOWED_RECEIPT_STATEMENTS:
                raise BatchError(
                    f"RECEIPT_LINE: {label}:{lineno}: expected key=value or an allowed exact statement"
                )
            if stripped in statements:
                raise BatchError(f"RECEIPT_DUPLICATE_STATEMENT: {label}:{lineno}: {stripped!r}")
            statements.append(stripped)
            continue

        key, value = raw_line.split("=", 1)
        key = key.strip(" ")
        value = value.strip(" ")
        if not RECEIPT_KEY_RE.fullmatch(key):
            raise BatchError(f"RECEIPT_KEY: {label}:{lineno}: invalid key {key!r}")
        if key in fields:
            raise BatchError(f"RECEIPT_DUPLICATE_KEY: {label}:{lineno}: duplicate key {key!r}")
        fields[key] = value

    required = ("id", "kind", "operation", "claimed_from", "authenticated_player")
    missing = [key for key in required if not fields.get(key)]
    if missing:
        raise BatchError(f"RECEIPT_MISSING_FIELD: {label}: {', '.join(missing)}")
    receipt_id = fields["id"]
    if not ticket_contract.ID_RE.fullmatch(receipt_id):
        raise BatchError(f"RECEIPT_ID: {label}: invalid id")
    if path.stem != receipt_id:
        raise BatchError(
            f"RECEIPT_FILENAME_ID_MISMATCH: {label}: {path.stem!r} != {receipt_id!r}"
        )
    for digest_key in ("ticket_sha256", "action_sha256"):
        value = fields.get(digest_key)
        if value is not None and not SHA256_RE.fullmatch(value):
            raise BatchError(f"RECEIPT_DIGEST: {label}: {digest_key} must be lowercase SHA-256")
    if "authenticated_player" in fields and fields["authenticated_player"] != "UNKNOWN":
        raise BatchError(
            f"RECEIPT_AUTHENTICATION_CLAIM: {label}: authenticated_player must be UNKNOWN"
        )
    if "claimed_from" in fields and not fields["claimed_from"]:
        raise BatchError(f"RECEIPT_CLAIMED_FROM: {label}: claimed_from must be non-empty")
    return Receipt(fields=fields, statements=tuple(statements))


def freeze_directory(directory: Path, *, reserved_names: frozenset[str]) -> list[Path]:
    """Freeze one directory name-set once; later arrivals wait for a later run."""
    directory = Path(directory)
    try:
        before = directory.lstat()
    except OSError as exc:
        raise BatchError(f"DIRECTORY_STAT_ERROR: {directory}: {_errno_name(exc)}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise BatchError(f"DIRECTORY_SYMLINK: {directory}: directory must not be a symlink")
    if not stat.S_ISDIR(before.st_mode):
        raise BatchError(f"DIRECTORY_NOT_DIRECTORY: {directory}: expected a directory")
    try:
        names = os.listdir(directory)
    except OSError as exc:
        raise BatchError(f"DIRECTORY_LIST_ERROR: {directory}: {_errno_name(exc)}") from exc
    try:
        after = directory.lstat()
    except OSError as exc:
        raise BatchError(f"DIRECTORY_CHANGED: {directory}: disappeared during listing") from exc
    if _stable_generation(before) != _stable_generation(after):
        raise BatchError(f"DIRECTORY_CHANGED: {directory}: changed during name-set freeze")
    selected = sorted(
        name
        for name in names
        if name.endswith(".txt") and name not in reserved_names
    )
    return [directory / name for name in selected]


def _preflight_new_output(path: Path) -> None:
    path = Path(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BatchError(f"OUTPUT_STAT_ERROR: {path}: {_errno_name(exc)}") from exc
    raise BatchError(f"OUTPUT_EXISTS: {path}: refusing overwrite or final-component symlink")


def write_new_file(path: Path, data: bytes) -> None:
    """Create one new ordinary output with O_EXCL and no final symlink following."""
    path = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BatchError(f"OUTPUT_CREATE_ERROR: {path}: {_errno_name(exc)}") from exc
    created_identity = _stable_identity(os.fstat(fd))
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise BatchError(f"OUTPUT_WRITE_ERROR: {path}: zero-byte write")
            view = view[written:]
        os.fsync(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != len(data):
            raise BatchError(f"OUTPUT_VERIFY_ERROR: {path}: output size/type mismatch")
    except BaseException:
        os.close(fd)
        try:
            current = path.lstat()
            if _stable_identity(current) == created_identity:
                path.unlink()
        except OSError:
            pass
        raise
    else:
        os.close(fd)


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _load_input_file(path: Path, *, label: str, max_bytes: int) -> bytes:
    return read_bounded_regular(path, label=label, role="verification", max_bytes=max_bytes).data
