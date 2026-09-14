import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import command_batch as cb
from command_batch_compile import compile_batch as _compile_batch_unbound
from command_batch_packet import verify_packet as _verify_packet_unbound

# Preserve the actual public exact-Git entry points for the dedicated authority
# boundary suite, then point the legacy parser/reconciliation harness at the
# implementation-level path API it is intended to exercise.
PUBLIC_COMPILE_BATCH = cb.compile_batch
PUBLIC_VERIFY_PACKET = cb.verify_packet
cb.compile_batch = _compile_batch_unbound
cb.verify_packet = _verify_packet_unbound


def surface(ticket_id: str, *, claimed_from: str = "GROK", extra: str = "") -> str:
    return (
        f"id={ticket_id}\n"
        "kind=surface\n"
        "approved=YES\n"
        f"claimed_from={claimed_from}\n"
        "authenticated_player=UNKNOWN\n"
        f"{extra}"
    )


def say(
    ticket_id: str,
    *,
    sender: str = "GROK",
    recipient: str = "KITE",
    body: str = "hello there",
    claimed_from: str = "GROK",
    owner_ok: str = "",
    extra: str = "",
) -> str:
    return (
        f"id={ticket_id}\n"
        "kind=say\n"
        "approved=YES\n"
        f"from={sender}\n"
        f"to={recipient}\n"
        f"claimed_from={claimed_from}\n"
        "authenticated_player=UNKNOWN\n"
        f"owner_ok={owner_ok}\n"
        f"{extra}"
        "---\n"
        f"{body}\n"
    )


def receipt(
    ticket_id: str,
    *,
    kind: str,
    ticket_sha256: str | None = None,
    action_sha256: str | None = None,
    operation: str | None = None,
    claimed_from: str = "GROK",
    extra: str = "",
) -> str:
    lines = [
        "RECEIPT",
        f"operation={operation or kind}",
        f"id={ticket_id}",
        f"kind={kind}",
        f"claimed_from={claimed_from}",
        "authenticated_player=UNKNOWN",
    ]
    if ticket_sha256 is not None:
        lines.append(f"ticket_sha256={ticket_sha256}")
    if action_sha256 is not None:
        lines.append(f"action_sha256={action_sha256}")
    if extra:
        lines.extend(extra.rstrip("\n").split("\n"))
    lines.append("HTTP is not the computer")
    return "\n".join(lines) + "\n"


def write_utf8(path: Path, text: str) -> None:
    """Write exact UTF-8 bytes without platform newline translation."""
    path.write_bytes(text.encode("utf-8"))


class BatchHarness:
    def __init__(self, root: Path):
        self.root = root
        self.commands = root / "COMMANDS"
        self.receipts = self.commands / "RECEIPTS"
        self.commands.mkdir(parents=True)
        self.receipts.mkdir()

    def write_ticket(self, ticket_id: str, text: str) -> Path:
        path = self.commands / f"{ticket_id}.txt"
        path.write_bytes(text.encode("utf-8"))
        return path

    def write_receipt(self, ticket_id: str, text: str) -> Path:
        path = self.receipts / f"{ticket_id}.txt"
        path.write_bytes(text.encode("utf-8"))
        return path

    def ticket_paths(self) -> list[Path]:
        return cb.freeze_directory(self.commands, reserved_names=frozenset())

    def receipt_paths(self) -> list[Path]:
        return cb.freeze_directory(self.receipts, reserved_names=frozenset())

    def compile(self, *, source_ref: str = "a" * 40) -> dict:
        return cb.compile_batch(
            self.ticket_paths(),
            self.receipt_paths(),
            source_ref=source_ref,
            ticket_root=self.commands,
            receipt_root=self.receipts,
        )
