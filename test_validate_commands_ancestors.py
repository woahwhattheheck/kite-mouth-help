from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import validate_commands as vc


SURFACE = """\
id=driveprobe1
kind=surface
approved=YES
claimed_from=GROK
authenticated_player=UNKNOWN
"""


class TicketAncestorContainmentTests(unittest.TestCase):
    def test_symlinked_ticket_ancestor_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            (outside / "driveprobe1.txt").write_bytes(SURFACE.encode("utf-8"))
            commands = root / "COMMANDS"
            try:
                commands.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks unavailable")
            with self.assertRaisesRegex(vc.TicketError, "ancestor must not be"):
                vc.read_ticket_file(commands / "driveprobe1.txt")

    def test_parent_generation_swap_with_same_ticket_inode_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = root / "COMMANDS"
            replacement = root / "replacement"
            commands.mkdir()
            replacement.mkdir()
            path = commands / "driveprobe1.txt"
            path.write_bytes(SURFACE.encode("utf-8"))
            try:
                os.link(path, replacement / "driveprobe1.txt")
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")
            old_commands = root / "old-COMMANDS"
            real_open = os.open
            swapped = False

            def replacing_parent_open(open_path, flags, *args, **kwargs):
                nonlocal swapped
                if not swapped and Path(open_path) == path:
                    commands.rename(old_commands)
                    replacement.rename(commands)
                    swapped = True
                return real_open(open_path, flags, *args, **kwargs)

            with mock.patch.object(vc.os, "open", side_effect=replacing_parent_open):
                with self.assertRaisesRegex(
                    vc.TicketError, "ancestor changed while being read"
                ):
                    vc.read_ticket_file(path)


if __name__ == "__main__":
    unittest.main()
