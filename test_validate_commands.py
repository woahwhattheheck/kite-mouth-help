import contextlib
import io
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

SAY = """\
id=say123
kind=say
approved=YES
from=GROK
to=KITE
claimed_from=GROK
authenticated_player=UNKNOWN
owner_ok=
---
hello there
"""


class TicketValidationTests(unittest.TestCase):
    def test_current_surface_shape_is_valid(self):
        ticket = vc.parse_ticket(SURFACE)
        vc.validate_ticket(ticket, path=Path("driveprobe1.txt"))

    def test_say_shape_is_valid(self):
        ticket = vc.parse_ticket(SAY)
        vc.validate_ticket(ticket, path=Path("say123.txt"))
        self.assertEqual(ticket.body, "hello there")

    def test_duplicate_header_key_is_rejected(self):
        text = SURFACE + "approved=NO\n"
        with self.assertRaisesRegex(vc.TicketError, "duplicate key 'approved'"):
            vc.parse_ticket(text)

    def test_unsafe_id_and_filename_mismatch_are_rejected(self):
        unsafe = SURFACE.replace("id=driveprobe1", "id=../driveprobe1")
        with self.assertRaisesRegex(vc.TicketError, "id must be"):
            vc.validate_ticket(vc.parse_ticket(unsafe), path=Path("driveprobe1.txt"))

        with self.assertRaisesRegex(vc.TicketError, "filename/id mismatch"):
            vc.validate_ticket(vc.parse_ticket(SURFACE), path=Path("other.txt"))

    def test_authority_fields_are_exact(self):
        unapproved = SURFACE.replace("approved=YES", "approved=yes")
        with self.assertRaisesRegex(vc.TicketError, "approved must be exactly YES"):
            vc.validate_ticket(vc.parse_ticket(unapproved), path=Path("driveprobe1.txt"))

        authenticated = SURFACE.replace("authenticated_player=UNKNOWN", "authenticated_player=GROK")
        with self.assertRaisesRegex(vc.TicketError, "authenticated_player must be exactly UNKNOWN"):
            vc.validate_ticket(vc.parse_ticket(authenticated), path=Path("driveprobe1.txt"))

    def test_say_requires_body_and_kite_to_grok_owner_ratification(self):
        no_body = SAY.rsplit("---", 1)[0]
        with self.assertRaisesRegex(vc.TicketError, "say ticket requires non-empty body"):
            vc.validate_ticket(vc.parse_ticket(no_body), path=Path("say123.txt"))

        reverse = SAY.replace("from=GROK\nto=KITE", "from=KITE\nto=GROK")
        with self.assertRaisesRegex(vc.TicketError, "owner_ok=BRYCE"):
            vc.validate_ticket(vc.parse_ticket(reverse), path=Path("say123.txt"))

        ratified = reverse.replace("owner_ok=", "owner_ok=BRYCE")
        vc.validate_ticket(vc.parse_ticket(ratified), path=Path("say123.txt"))

    def test_surface_rejects_hidden_body(self):
        with self.assertRaisesRegex(vc.TicketError, "surface tickets must not contain"):
            vc.validate_ticket(vc.parse_ticket(SURFACE + "---\nhidden\n"), path=Path("driveprobe1.txt"))

    def test_repository_validation_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first" / "same.txt"
            second = root / "second" / "same.txt"
            first.parent.mkdir()
            second.parent.mkdir()
            payload = SURFACE.replace("driveprobe1", "same")
            first.write_bytes(payload.encode("utf-8"))
            second.write_bytes(payload.encode("utf-8"))
            with self.assertRaisesRegex(vc.TicketError, "duplicate id 'same'"):
                vc.validate_paths([first, second])

    def test_cli_scans_real_command_directory_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "driveprobe1.txt"
            path.write_bytes(SURFACE.encode("utf-8"))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(vc.main([str(path)]), 0)
            self.assertEqual(stdout.getvalue().strip(), "PASS: 1 command ticket(s)")

    def test_descriptor_reader_accepts_exact_utf8_lf_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "driveprobe1.txt"
            path.write_bytes(SURFACE.encode("utf-8"))
            self.assertEqual(vc.read_ticket_file(path), SURFACE)

    def test_rejects_bom_invalid_utf8_and_oversize(self):
        cases = [
            (b"\xef\xbb\xbf" + SURFACE.encode(), "BOM"),
            (SURFACE.encode() + b"\xff", "strict UTF-8"),
            (b"x" * (vc.MAX_TICKET_BYTES + 1), "exceeds"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "driveprobe1.txt"
            for payload, message in cases:
                with self.subTest(message=message):
                    path.write_bytes(payload)
                    with self.assertRaisesRegex(vc.TicketError, message):
                        vc.read_ticket_file(path)

    def test_rejects_parser_differential_separators_and_controls_anywhere(self):
        cases = {
            "CRLF": SURFACE.replace("\n", "\r\n", 1),
            "NEL": SURFACE.replace("\n", "\u0085", 1),
            "LINE_SEPARATOR": SURFACE.replace("\n", "\u2028", 1),
            "PARAGRAPH_SEPARATOR": SURFACE.replace("\n", "\u2029", 1),
            "NUL_HEADER": SURFACE.replace("GROK", "GROK\x00"),
            "TAB_BODY": SAY.replace("hello there", "hello\tthere"),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(vc.TicketError, "forbidden control/separator"):
                    vc.parse_ticket(text)

    def test_final_symlink_and_nonregular_paths_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "real.txt"
            target.write_bytes(SURFACE.encode("utf-8"))
            link = root / "driveprobe1.txt"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaisesRegex(vc.TicketError, "must not be a symlink"):
                    vc.read_ticket_file(link)

            directory = root / "directory.txt"
            directory.mkdir()
            with self.assertRaisesRegex(vc.TicketError, "regular file"):
                vc.read_ticket_file(directory)

    def test_path_replacement_between_lstat_and_open_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "driveprobe1.txt"
            replacement = root / "replacement.tmp"
            path.write_bytes(SURFACE.encode("utf-8"))
            replacement.write_bytes(SURFACE.replace("GROK", "KITE").encode("utf-8"))
            real_open = os.open
            swapped = False

            def replacing_open(open_path, flags, *args, **kwargs):
                nonlocal swapped
                if not swapped and Path(open_path) == path:
                    os.replace(replacement, path)
                    swapped = True
                return real_open(open_path, flags, *args, **kwargs)

            with mock.patch.object(vc.os, "open", side_effect=replacing_open):
                with self.assertRaisesRegex(vc.TicketError, "changed before open"):
                    vc.read_ticket_file(path)

    def test_discovery_does_not_silently_skip_txt_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fake.txt").mkdir()
            paths = vc.discover_ticket_paths(root)
            self.assertEqual(paths, [root / "fake.txt"])
            with self.assertRaisesRegex(vc.TicketError, "regular file"):
                vc.validate_paths(paths)


if __name__ == "__main__":
    unittest.main()
