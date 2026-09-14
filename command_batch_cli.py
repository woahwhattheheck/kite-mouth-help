"""Command-line interface for compiling and verifying command batch ledgers."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
import validate_commands as ticket_contract
from command_batch_compile import compile_batch
from command_batch_model import BatchError, MAX_PACKET_BYTES, MAX_SUMMARY_BYTES, RECEIPT_RESERVED_NAMES
from command_batch_packet import packet_bytes, render_summary, verify_packet
from command_batch_sources import (
    _load_input_file, _path_key, _preflight_new_output, freeze_directory, write_new_file,
)


def _freeze_cli_inputs(commands_dir: Path, receipts_dir: Path) -> tuple[list[Path], list[Path]]:
    tickets = freeze_directory(commands_dir, reserved_names=ticket_contract.RESERVED_NAMES)
    receipts = freeze_directory(receipts_dir, reserved_names=RECEIPT_RESERVED_NAMES)
    return tickets, receipts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--commands-dir", type=Path, default=Path("COMMANDS"))
        subparser.add_argument("--receipts-dir", type=Path, default=Path("COMMANDS/RECEIPTS"))
        subparser.add_argument("--source-ref", default="UNPINNED")

    compile_parser = subparsers.add_parser("compile", help="compile a new packet and summary")
    common(compile_parser)
    compile_parser.add_argument("--packet-out", type=Path, required=True)
    compile_parser.add_argument("--summary-out", type=Path, required=True)
    compile_parser.add_argument("--fail-on-hold", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="recompile and verify an existing packet")
    common(verify_parser)
    verify_parser.add_argument("--packet", type=Path, required=True)
    verify_parser.add_argument("--summary", type=Path)
    verify_parser.add_argument("--fail-on-hold", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        ticket_paths, receipt_paths = _freeze_cli_inputs(args.commands_dir, args.receipts_dir)
        if args.command == "compile":
            if _path_key(args.packet_out) == _path_key(args.summary_out):
                raise BatchError("OUTPUT_COLLISION: packet and summary paths must differ")
            _preflight_new_output(args.packet_out)
            _preflight_new_output(args.summary_out)
            packet = compile_batch(
                ticket_paths,
                receipt_paths,
                source_ref=args.source_ref,
                ticket_root=args.commands_dir,
                receipt_root=args.receipts_dir,
            )
            write_new_file(args.packet_out, packet_bytes(packet))
            write_new_file(args.summary_out, render_summary(packet).encode("utf-8"))
            print(f"COMPILED: {packet['payload']['state']} {packet['payload_sha256']}")
        else:
            packet_data = _load_input_file(
                args.packet, label="packet", max_bytes=MAX_PACKET_BYTES
            )
            summary_data = None
            if args.summary is not None:
                summary_data = _load_input_file(
                    args.summary, label="summary", max_bytes=MAX_SUMMARY_BYTES
                )
            packet = verify_packet(
                packet_data,
                ticket_paths,
                receipt_paths,
                source_ref=args.source_ref,
                ticket_root=args.commands_dir,
                receipt_root=args.receipts_dir,
                summary=summary_data,
            )
            print(f"VERIFIED: {packet['payload']['state']} {packet['payload_sha256']}")
        if args.fail_on_hold and packet["payload"]["state"] == "HOLD":
            return 3
        return 0
    except (BatchError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
