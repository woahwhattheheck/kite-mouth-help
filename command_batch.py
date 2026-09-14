#!/usr/bin/env python3
"""Compile and verify a deterministic, non-executing command batch ledger.

This compatibility facade exposes the public API while implementation is split
into small reviewable modules. No module executes a ticket or contacts a device
or network.
"""
from command_batch_model import *
from command_batch_model import _canonical_json_bytes
from command_batch_sources import (
    _load_input_file, _path_key, _preflight_new_output, freeze_directory,
    parse_receipt_bytes, read_bounded_regular, write_new_file,
)
from command_batch_compile import compile_batch
from command_batch_git import FrozenGitSnapshot, frozen_git_snapshot
from command_batch_packet import packet_bytes, parse_packet_bytes, render_summary, verify_packet
from command_batch_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
