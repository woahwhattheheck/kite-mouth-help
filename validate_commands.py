#!/usr/bin/env python3
"""Fail-closed validation for kite-mouth-help command tickets.

This module only reads ticket text. It never executes a command or contacts a
network service.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
KEY_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
RESERVED_NAMES = frozenset({"HOW.txt", "TEMPLATE_SAY.txt", "TEMPLATE_SURFACE.txt", "inbox.txt"})
COMMON_REQUIRED = frozenset({"id", "kind", "approved", "claimed_from", "authenticated_player"})
MAX_TICKET_BYTES = 64 * 1024
_FORBIDDEN_SEPARATORS = frozenset({"\u0085", "\u2028", "\u2029", "\ufeff"})


class TicketError(ValueError):
    """Raised when a command ticket violates the repository contract."""


@dataclass(frozen=True)
class Ticket:
    fields: dict[str, str]
    body: str | None


def _validate_text_grammar(text: str) -> None:
    """Require the documented LF-delimited printable-text grammar."""
    for char in text:
        if char == "\n":
            continue
        if char in _FORBIDDEN_SEPARATORS or unicodedata.category(char) == "Cc":
            raise TicketError(f"ticket contains forbidden control/separator U+{ord(char):04X}")


def _stable_stat_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _stable_file_generation(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)),
    )


def _decode_ticket_bytes(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        raise TicketError("ticket must not use a UTF-8 BOM")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise TicketError(f"ticket is not strict UTF-8: {exc}") from exc
    _validate_text_grammar(text)
    return text


def read_ticket_file(path: Path) -> str:
    """Read one bounded ticket from one stable ordinary-file generation."""
    path = Path(path)
    try:
        before_path = path.lstat()
    except OSError as exc:
        raise TicketError(f"{path}: cannot stat ticket: {exc}") from exc
    if stat.S_ISLNK(before_path.st_mode):
        raise TicketError(f"{path}: ticket path must not be a symlink")
    if not stat.S_ISREG(before_path.st_mode):
        raise TicketError(f"{path}: ticket path must be a regular file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise TicketError(f"{path}: cannot open ticket safely: {exc}") from exc

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise TicketError(f"{path}: opened ticket must be a regular file")
        if _stable_stat_identity(before_path) != _stable_stat_identity(opened):
            raise TicketError(f"{path}: ticket path changed before open")
        if opened.st_size > MAX_TICKET_BYTES:
            raise TicketError(f"{path}: ticket exceeds {MAX_TICKET_BYTES} byte limit")

        chunks: list[bytes] = []
        remaining = MAX_TICKET_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(16 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_TICKET_BYTES:
            raise TicketError(f"{path}: ticket exceeds {MAX_TICKET_BYTES} byte limit")

        after_fd = os.fstat(fd)
        if _stable_file_generation(opened) != _stable_file_generation(after_fd):
            raise TicketError(f"{path}: ticket changed while being read")
        try:
            after_path = path.lstat()
        except OSError as exc:
            raise TicketError(f"{path}: ticket path changed after read: {exc}") from exc
        if _stable_stat_identity(after_path) != _stable_stat_identity(after_fd):
            raise TicketError(f"{path}: ticket path changed after read")
    finally:
        os.close(fd)

    return _decode_ticket_bytes(data)


def parse_ticket(text: str) -> Ticket:
    """Parse one ticket without interpreting or executing it."""
    _validate_text_grammar(text)
    fields: dict[str, str] = {}
    body_lines: list[str] | None = None

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    for lineno, raw_line in enumerate(lines, 1):
        if body_lines is not None:
            body_lines.append(raw_line)
            continue

        stripped = raw_line.strip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---":
            body_lines = []
            continue
        if "=" not in raw_line:
            raise TicketError(f"line {lineno}: expected key=value or ---")

        key, value = raw_line.split("=", 1)
        key = key.strip(" ")
        value = value.strip(" ")
        if not KEY_RE.fullmatch(key):
            raise TicketError(f"line {lineno}: invalid key {key!r}")
        if key in fields:
            raise TicketError(f"line {lineno}: duplicate key {key!r}")
        fields[key] = value

    body = None if body_lines is None else "\n".join(body_lines)
    return Ticket(fields=fields, body=body)


def validate_ticket(ticket: Ticket, *, path: Path | None = None) -> None:
    fields = ticket.fields
    missing = sorted(COMMON_REQUIRED - fields.keys())
    if missing:
        raise TicketError("missing required field(s): " + ", ".join(missing))

    ticket_id = fields["id"]
    if not ID_RE.fullmatch(ticket_id):
        raise TicketError("id must be 1-128 characters using only letters, digits, '.', '_' or '-', starting alphanumeric")
    if path is not None and path.stem != ticket_id:
        raise TicketError(f"filename/id mismatch: {path.stem!r} != {ticket_id!r}")

    if fields["approved"] != "YES":
        raise TicketError("approved must be exactly YES")
    if not fields["claimed_from"]:
        raise TicketError("claimed_from must be non-empty")
    if fields["authenticated_player"] != "UNKNOWN":
        raise TicketError("authenticated_player must be exactly UNKNOWN")

    kind = fields["kind"]
    if kind == "surface":
        if ticket.body is not None:
            raise TicketError("surface tickets must not contain a body separator/body")
        return

    if kind == "say":
        for key in ("from", "to"):
            if not fields.get(key):
                raise TicketError(f"say ticket requires non-empty {key}")
        if ticket.body is None or not ticket.body.strip():
            raise TicketError("say ticket requires non-empty body after ---")
        if fields["from"] == "KITE" and fields["to"] == "GROK" and fields.get("owner_ok") != "BRYCE":
            raise TicketError("KITE->GROK say requires owner_ok=BRYCE")
        return

    raise TicketError(f"unsupported kind {kind!r}")


def discover_ticket_paths(commands_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in commands_dir.glob("*.txt")
        if path.name not in RESERVED_NAMES
    )


def validate_paths(paths: Iterable[Path]) -> int:
    seen_ids: dict[str, Path] = {}
    count = 0
    for path in paths:
        count += 1
        text = read_ticket_file(path)
        ticket = parse_ticket(text)
        validate_ticket(ticket, path=path)
        ticket_id = ticket.fields["id"]
        prior = seen_ids.get(ticket_id)
        if prior is not None:
            raise TicketError(f"duplicate id {ticket_id!r}: {prior} and {path}")
        seen_ids[ticket_id] = path
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="ticket files; defaults to COMMANDS/*.txt")
    args = parser.parse_args(argv)
    paths = args.paths or discover_ticket_paths(Path(__file__).resolve().parent / "COMMANDS")
    try:
        count = validate_paths(paths)
    except TicketError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"PASS: {count} command ticket(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
