import contextlib
import io
import tempfile
import unittest
from pathlib import Path

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
            first.write_text(payload, encoding="utf-8")
            second.write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(vc.TicketError, "duplicate id 'same'"):
                vc.validate_paths([first, second])

    def test_cli_scans_real_command_directory_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "driveprobe1.txt"
            path.write_text(SURFACE, encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(vc.main([str(path)]), 0)
            self.assertEqual(stdout.getvalue().strip(), "PASS: 1 command ticket(s)")


if __name__ == "__main__":
    unittest.main()
