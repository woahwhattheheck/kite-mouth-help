#!/usr/bin/env python3
"""Ancestor-contained entry point for command-ticket validation.

The parser and ticket contract remain in ``validate_commands_core``. This
module hardens the filesystem read boundary so a ticket is accepted only when
its final file and every pathname ancestor remain in one ordinary generation.
It still performs validation only: no command, network, provider, or device
mutation is authorized here.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import validate_commands_core as _core
from validate_commands_core import *  # noqa: F401,F403 - preserve the public API


def _is_symlink_or_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _snapshot_ticket_ancestors(path: Path) -> list[tuple[Path, tuple[int, int, int]]]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    ancestors = list(reversed(absolute.parent.parents))
    ancestors.append(absolute.parent)
    snapshot: list[tuple[Path, tuple[int, int, int]]] = []
    for ancestor in ancestors:
        try:
            info = ancestor.lstat()
        except OSError as exc:
            raise TicketError(
                f"{path}: cannot stat ticket ancestor {ancestor}: {exc}"
            ) from exc
        if _is_symlink_or_reparse(info):
            raise TicketError(
                f"{path}: ticket ancestor must not be a symlink or reparse point: {ancestor}"
            )
        if not stat.S_ISDIR(info.st_mode):
            raise TicketError(
                f"{path}: ticket ancestor must be a directory: {ancestor}"
            )
        snapshot.append((ancestor, _core._stable_stat_identity(info)))
    return snapshot


def _verify_ticket_ancestors(
    path: Path, snapshot: list[tuple[Path, tuple[int, int, int]]]
) -> None:
    for ancestor, expected in snapshot:
        try:
            info = ancestor.lstat()
        except OSError as exc:
            raise TicketError(
                f"{path}: ticket ancestor changed after read: {ancestor}: {exc}"
            ) from exc
        if _is_symlink_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise TicketError(
                f"{path}: ticket ancestor changed while being read: {ancestor}"
            )
        if _core._stable_stat_identity(info) != expected:
            raise TicketError(
                f"{path}: ticket ancestor changed while being read: {ancestor}"
            )


def read_ticket_file(path: Path) -> str:
    """Read one bounded ticket while retaining ancestor-generation evidence."""
    path = Path(path)
    ancestor_snapshot = _snapshot_ticket_ancestors(path)
    try:
        before_path = path.lstat()
    except OSError as exc:
        raise TicketError(f"{path}: cannot stat ticket: {exc}") from exc
    if _is_symlink_or_reparse(before_path):
        raise TicketError(
            f"{path}: ticket path must not be a symlink or reparse point"
        )
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
        if _core._stable_stat_identity(before_path) != _core._stable_stat_identity(opened):
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
        if _core._stable_file_generation(opened) != _core._stable_file_generation(after_fd):
            raise TicketError(f"{path}: ticket changed while being read")
        try:
            after_path = path.lstat()
        except OSError as exc:
            raise TicketError(f"{path}: ticket path changed after read: {exc}") from exc
        if _core._stable_stat_identity(after_path) != _core._stable_stat_identity(after_fd):
            raise TicketError(f"{path}: ticket path changed after read")
        _verify_ticket_ancestors(path, ancestor_snapshot)
    finally:
        os.close(fd)

    return _core._decode_ticket_bytes(data)


# Functions defined in the preserved core resolve globals in that module. Point
# their read boundary at this hardened implementation before exposing the API.
_core.read_ticket_file = read_ticket_file


if __name__ == "__main__":
    raise SystemExit(_core.main())
