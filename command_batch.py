#!/usr/bin/env python3
"""Compile and verify deterministic, non-executing command batch ledgers.

The public ``compile_batch`` / ``verify_packet`` API is exact-Git only: callers
supply one full commit SHA and these wrappers derive the complete selected source
set from that immutable commit. Path-subset compilation and verification remain
implementation details used by the parser test harness and CLI internals; they
are deliberately not re-exported as public authority entry points.

No module executes a ticket or contacts a device or network.
"""
from pathlib import Path

from command_batch_model import *
from command_batch_model import _canonical_json_bytes
from command_batch_sources import (
    _load_input_file, _path_key, _preflight_new_output, freeze_directory,
    parse_receipt_bytes, read_bounded_regular, write_new_file,
)
from command_batch_compile import compile_batch as _compile_batch_unbound
from command_batch_git import FrozenGitSnapshot, frozen_git_snapshot
from command_batch_packet import (
    packet_bytes,
    parse_packet_bytes,
    render_summary,
    verify_packet as _verify_packet_unbound,
)
from command_batch_cli import main


def compile_batch(
    *,
    git_repo: Path = Path("."),
    source_ref: str,
    commands_dir: Path = Path("COMMANDS"),
    receipts_dir: Path = Path("COMMANDS/RECEIPTS"),
):
    """Compile the complete selected source set from one exact Git commit.

    There is intentionally no ticket-path or receipt-path parameter. A caller
    cannot omit a committed ticket while still using this public authoritative
    entry point.
    """
    with frozen_git_snapshot(
        git_repo,
        source_ref,
        commands_dir=commands_dir,
        receipts_dir=receipts_dir,
    ) as snapshot:
        packet = _compile_batch_unbound(
            snapshot.ticket_paths,
            snapshot.receipt_paths,
            source_ref=snapshot.source_ref,
            ticket_root=snapshot.ticket_root,
            receipt_root=snapshot.receipt_root,
        )
        snapshot.assert_packet_sources(packet)
        return packet


def verify_packet(
    data: bytes,
    *,
    git_repo: Path = Path("."),
    source_ref: str,
    commands_dir: Path = Path("COMMANDS"),
    receipts_dir: Path = Path("COMMANDS/RECEIPTS"),
    summary: bytes | None = None,
):
    """Verify a packet only against the complete set from one exact Git commit."""
    with frozen_git_snapshot(
        git_repo,
        source_ref,
        commands_dir=commands_dir,
        receipts_dir=receipts_dir,
    ) as snapshot:
        packet = _verify_packet_unbound(
            data,
            snapshot.ticket_paths,
            snapshot.receipt_paths,
            source_ref=snapshot.source_ref,
            ticket_root=snapshot.ticket_root,
            receipt_root=snapshot.receipt_root,
            summary=summary,
        )
        snapshot.assert_packet_sources(packet)
        return packet


if __name__ == "__main__":
    raise SystemExit(main())
