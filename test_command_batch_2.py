from test_command_batch_support import *


class CommandBatchTests2(unittest.TestCase):
    def test_legacy_unbound_receipt_is_visible_but_cannot_green_ticket(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket("surface1", surface("surface1"))
                harness.write_receipt("surface1", receipt("surface1", kind="surface"))
                packet = harness.compile()
                self.assertEqual(packet["payload"]["state"], "HOLD")
                self.assertIn(
                    "LEGACY_RECEIPT_UNBOUND",
                    {reason["code"] for reason in packet["payload"]["tickets"][0]["reasons"]},
                )

    def test_changed_same_id_ticket_bytes_fail_receipt_binding(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                original = surface("surface1")
                original_digest = hashlib.sha256(original.encode()).hexdigest()
                harness.write_ticket("surface1", surface("surface1", extra="note=changed\n"))
                harness.write_receipt(
                    "surface1",
                    receipt("surface1", kind="surface", ticket_sha256=original_digest),
                )
                packet = harness.compile()
                codes = {reason["code"] for reason in packet["payload"]["tickets"][0]["reasons"]}
                self.assertIn("RECEIPT_TICKET_DIGEST_MISMATCH", codes)

    def test_action_digest_mismatch_is_a_hold(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                ticket_path = harness.write_ticket("say1", say("say1"))
                harness.write_receipt(
                    "say1",
                    receipt(
                        "say1",
                        kind="say",
                        ticket_sha256=hashlib.sha256(ticket_path.read_bytes()).hexdigest(),
                        action_sha256="0" * 64,
                    ),
                )
                packet = harness.compile()
                codes = {reason["code"] for reason in packet["payload"]["receipts"][0]["reasons"]}
                self.assertIn("RECEIPT_ACTION_DIGEST_MISMATCH", codes)

    def test_receipt_kind_and_operation_mismatch_hold_both_sides(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                ticket_path = harness.write_ticket("surface1", surface("surface1"))
                harness.write_receipt(
                    "surface1",
                    receipt(
                        "surface1",
                        kind="say",
                        operation="say",
                        ticket_sha256=hashlib.sha256(ticket_path.read_bytes()).hexdigest(),
                    ),
                )
                packet = harness.compile()
                for section in ("tickets", "receipts"):
                    codes = {reason["code"] for reason in packet["payload"][section][0]["reasons"]}
                    self.assertIn("RECEIPT_TICKET_MISMATCH", codes)

    def test_orphan_receipt_holds_batch(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_receipt(
                    "ghost",
                    receipt("ghost", kind="surface", ticket_sha256="0" * 64),
                )
                packet = harness.compile()
                self.assertEqual(packet["payload"]["state"], "HOLD")
                self.assertIn(
                    "ORPHAN_RECEIPT",
                    {reason["code"] for reason in packet["payload"]["receipts"][0]["reasons"]},
                )

    def test_duplicate_receipt_ids_across_explicit_paths_hold(self):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                commands = root / "commands"
                commands.mkdir()
                ticket_path = commands / "same.txt"
                ticket_path.write_text(surface("same"), encoding="utf-8")
                digest = hashlib.sha256(ticket_path.read_bytes()).hexdigest()
                first_dir = root / "receipts-a"
                second_dir = root / "receipts-b"
                first_dir.mkdir()
                second_dir.mkdir()
                first = first_dir / "same.txt"
                second = second_dir / "same.txt"
                payload = receipt("same", kind="surface", ticket_sha256=digest)
                first.write_text(payload, encoding="utf-8")
                second.write_text(payload, encoding="utf-8")
                packet = cb.compile_batch(
                    [ticket_path],
                    [first, second],
                    source_ref="x",
                    ticket_root=commands,
                    receipt_root=root,
                )
                self.assertEqual(packet["payload"]["state"], "HOLD")
                self.assertTrue(
                    all(
                        "DUPLICATE_RECEIPT_ID" in {reason["code"] for reason in item["reasons"]}
                        for item in packet["payload"]["receipts"]
                    )
                )
                ticket_codes = {
                    reason["code"] for reason in packet["payload"]["tickets"][0]["reasons"]
                }
                self.assertIn("DUPLICATE_RECEIPT_ID", ticket_codes)
                self.assertEqual(packet["payload"]["tickets"][0]["status"], "HOLD")

    def test_two_bound_receipts_for_same_action_expose_historical_duplicate(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                first = harness.write_ticket("surface1", surface("surface1"))
                second = harness.write_ticket("surface2", surface("surface2"))
                harness.write_receipt(
                    "surface1",
                    receipt(
                        "surface1",
                        kind="surface",
                        ticket_sha256=hashlib.sha256(first.read_bytes()).hexdigest(),
                    ),
                )
                harness.write_receipt(
                    "surface2",
                    receipt(
                        "surface2",
                        kind="surface",
                        ticket_sha256=hashlib.sha256(second.read_bytes()).hexdigest(),
                    ),
                )
                packet = harness.compile()
                self.assertEqual(packet["payload"]["state"], "HOLD")
                for item in packet["payload"]["tickets"] + packet["payload"]["receipts"]:
                    self.assertIn(
                        "DUPLICATE_RECEIPTED_ACTION",
                        {reason["code"] for reason in item["reasons"]},
                    )

    def test_duplicate_ticket_ids_across_explicit_paths_hold(self):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                first_dir = root / "tickets-a"
                second_dir = root / "tickets-b"
                first_dir.mkdir()
                second_dir.mkdir()
                first = first_dir / "same.txt"
                second = second_dir / "same.txt"
                first.write_text(surface("same"), encoding="utf-8")
                second.write_text(surface("same"), encoding="utf-8")
                packet = cb.compile_batch([first, second], [], source_ref="x", ticket_root=root)
                self.assertEqual(packet["payload"]["state"], "HOLD")
                self.assertTrue(
                    all(
                        "DUPLICATE_TICKET_ID" in {reason["code"] for reason in item["reasons"]}
                        for item in packet["payload"]["tickets"]
                    )
                )
