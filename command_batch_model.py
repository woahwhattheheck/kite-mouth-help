"""Shared constants and deterministic primitives for command batch ledgers."""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import validate_commands as ticket_contract


SCHEMA = "kite-mouth.command-batch/v1"


POLICY_VERSION = "command-batch-policy/v1"


MAX_INPUT_BYTES = 64 * 1024


MAX_PACKET_BYTES = 2 * 1024 * 1024


MAX_SUMMARY_BYTES = 2 * 1024 * 1024


MAX_SOURCE_REF_CHARS = 256


RECEIPT_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")


SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


ALLOWED_RECEIPT_STATEMENTS = frozenset({"HTTP is not the computer"})


RECEIPT_RESERVED_NAMES = frozenset({"inbox.txt"})


NON_ACTION_FIELDS = frozenset({"id", "approved", "claimed_from", "authenticated_player"})


FORBIDDEN_SEPARATORS = frozenset({"\u0085", "\u2028", "\u2029", "\ufeff"})


AUTHORITY_CEILING = {
    "authenticate_claimed_from": False,
    "authorize_later_bytes": False,
    "contact_device_or_network": False,
    "dispatch_message": False,
    "execute_ticket": False,
    "grant_zero_authority": False,
    "write_host_receipt": False,
}


class BatchError(ValueError):
    """Raised when a batch operation cannot be completed safely."""


@dataclass(frozen=True)
class SourceBytes:
    role: str
    path: Path
    label: str
    data: bytes
    sha256: str

    @property
    def byte_count(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class Receipt:
    fields: dict[str, str]
    statements: tuple[str, ...]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json_bytes(value: Any, *, newline: bool = False) -> bytes:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return encoded + (b"\n" if newline else b"")


def _stable_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _stable_generation(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)),
    )


def _errno_name(exc: OSError) -> str:
    return getattr(exc, "strerror", None) or exc.__class__.__name__


def _decode_text(data: bytes, *, label: str, noun: str) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        raise BatchError(f"{noun}_BOM: {label}: UTF-8 BOM is forbidden")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BatchError(f"{noun}_UTF8: {label}: invalid UTF-8 at byte {exc.start}") from exc
    for char in text:
        if char == "\n":
            continue
        if char in FORBIDDEN_SEPARATORS or unicodedata.category(char) == "Cc":
            raise BatchError(
                f"{noun}_CONTROL: {label}: forbidden control/separator U+{ord(char):04X}"
            )
    return text


def _action_sha256(ticket: ticket_contract.Ticket) -> str:
    semantic_fields = {
        key: value
        for key, value in ticket.fields.items()
        if key not in NON_ACTION_FIELDS
    }
    semantic = {"body": ticket.body, "fields": semantic_fields}
    return _sha256(_canonical_json_bytes(semantic))


def _validate_source_ref(source_ref: str) -> str:
    if not isinstance(source_ref, str) or not source_ref or len(source_ref) > MAX_SOURCE_REF_CHARS:
        raise BatchError(f"SOURCE_REF: must be 1-{MAX_SOURCE_REF_CHARS} printable characters")
    for char in source_ref:
        if char in FORBIDDEN_SEPARATORS or unicodedata.category(char) == "Cc":
            raise BatchError("SOURCE_REF: contains forbidden control/separator")
    return source_ref


def _label_for(path: Path, *, root: Path | None, prefix: str) -> str:
    path = Path(path)
    if root is not None:
        try:
            relative = path.absolute().relative_to(Path(root).absolute())
        except ValueError:
            relative = Path(path.name)
    else:
        relative = Path(path.name)
    label = f"{prefix}/{relative.as_posix()}"
    if label.endswith("/") or "/../" in f"/{label}/":
        raise BatchError(f"SOURCE_LABEL: unsafe derived label for {path.name!r}")
    return label


def _labeled_paths(
    paths: Iterable[Path], *, root: Path | None, prefix: str
) -> list[tuple[str, Path]]:
    provisional = [(_label_for(Path(path), root=root, prefix=prefix), Path(path)) for path in paths]
    provisional.sort(key=lambda item: (item[0], str(item[1].absolute())))
    counts: dict[str, int] = {}
    for label, _ in provisional:
        counts[label] = counts.get(label, 0) + 1
    seen: dict[str, int] = {}
    result: list[tuple[str, Path]] = []
    for label, path in provisional:
        if counts[label] > 1:
            seen[label] = seen.get(label, 0) + 1
            label = f"{label}#{seen[label]}"
        result.append((label, path))
    return result


def _reason(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _add_reason(item: dict[str, Any], code: str, detail: str) -> None:
    candidate = _reason(code, detail)
    if candidate not in item["reasons"]:
        item["reasons"].append(candidate)


def _sort_reasons(item: dict[str, Any]) -> None:
    item["reasons"].sort(key=lambda entry: (entry["code"], entry["detail"]))


def _error_code(error: BatchError, fallback: str) -> tuple[str, str]:
    text = str(error)
    if ":" in text:
        code, detail = text.split(":", 1)
        if re.fullmatch(r"[A-Z0-9_]+", code):
            return code, detail.strip()
    return fallback, text
