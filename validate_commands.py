#!/usr/bin/env python3
"""Fail-closed validation for kite-mouth-help command tickets.

This module only reads ticket text. It never executes a command or contacts a
network service.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
KEY_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
RESERVED_NAMES = frozenset({"HOW.txt", "TEMPLATE_SAY.txt", "TEMPLATE_SURFACE.txt", "inbox.txt"})
COMMON_REQUIRED = frozenset({"id", "kind", "approved", "claimed_from", "authenticated_player"})


class TicketError(ValueError):
    """Raised when a command ticket violates the repository contract."""


@dataclass(frozen=True)
class Ticket:
    fields: dict[str, str]
    body: str | None


def _has_forbidden_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def parse_ticket(text: str) -> Ticket:
    """Parse one ticket without interpreting or executing it."""
    fields: dict[str, str] = {}
    body_lines: list[str] | None = None

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        if body_lines is not None:
            body_lines.append(raw_line)
            continue

        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---":
            body_lines = []
            continue
        if "=" not in raw_line:
            raise TicketError(f"line {lineno}: expected key=value or ---")

        key, value = raw_line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not KEY_RE.fullmatch(key):
            raise TicketError(f"line {lineno}: invalid key {key!r}")
        if key in fields:
            raise TicketError(f"line {lineno}: duplicate key {key!r}")
        if _has_forbidden_control(value):
            raise TicketError(f"line {lineno}: control character in {key!r}")
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
        if path.is_file() and path.name not in RESERVED_NAMES
    )


def validate_paths(paths: Iterable[Path]) -> int:
    seen_ids: dict[str, Path] = {}
    count = 0
    for path in paths:
        count += 1
        if path.is_symlink():
            raise TicketError(f"{path}: ticket path must not be a symlink")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise TicketError(f"{path}: cannot read UTF-8 ticket: {exc}") from exc
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
