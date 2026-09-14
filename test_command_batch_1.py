from test_command_batch_support import *


class CommandBatchTests1(unittest.TestCase):
    def test_empty_batch_is_explicit_and_non_actionable(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                packet = harness.compile()
                self.assertEqual(packet["payload"]["state"], "EMPTY_BATCH")
                self.assertEqual(packet["payload"]["counts"]["tickets_total"], 0)
                self.assertTrue(all(value is False for value in packet["payload"]["authority"].values()))

    def test_unreceipted_surface_is_ready_for_host_review(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket("surface1", surface("surface1"))
                packet = harness.compile()
                self.assertEqual(packet["payload"]["state"], "READY_FOR_HOST_BATCH_REVIEW")
                self.assertEqual(packet["payload"]["tickets"][0]["status"], "READY_FOR_HOST_REVIEW")
                self.assertEqual(packet["payload"]["blockers"], [])

    def test_unreceipted_say_is_ready_without_exposing_body(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket("say1", say("say1", body="private body"))
                packet = harness.compile()
                serialized = cb.packet_bytes(packet)
                self.assertEqual(packet["payload"]["state"], "READY_FOR_HOST_BATCH_REVIEW")
                self.assertNotIn(b"private body", serialized)
                self.assertRegex(packet["payload"]["tickets"][0]["action_sha256"], r"^[0-9a-f]{64}$")

    def test_kite_to_grok_requires_owner_ratification_from_existing_contract(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket(
                    "reverse",
                    say("reverse", sender="KITE", recipient="GROK", claimed_from="KITE"),
                )
                packet = harness.compile()
                ticket = packet["payload"]["tickets"][0]
                self.assertEqual(ticket["status"], "HOLD")
                self.assertIn("MALFORMED_TICKET", {reason["code"] for reason in ticket["reasons"]})

                write_utf8(
                    harness.commands / "reverse.txt",
                    say(
                        "reverse",
                        sender="KITE",
                        recipient="GROK",
                        claimed_from="KITE",
                        owner_ok="BRYCE",
                    ),
                )
                packet = harness.compile()
                self.assertEqual(packet["payload"]["tickets"][0]["status"], "READY_FOR_HOST_REVIEW")

    def test_distinct_ids_with_same_say_action_hold_both(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket("say1", say("say1", claimed_from="GROK"))
                harness.write_ticket("say2", say("say2", claimed_from="KITE"))
                packet = harness.compile()
                self.assertEqual(packet["payload"]["state"], "HOLD")
                tickets = packet["payload"]["tickets"]
                self.assertEqual(tickets[0]["action_sha256"], tickets[1]["action_sha256"])
                for ticket in tickets:
                    self.assertIn("DUPLICATE_ACTION", {reason["code"] for reason in ticket["reasons"]})

    def test_surface_actions_under_distinct_ids_are_duplicate(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket("surface1", surface("surface1"))
                harness.write_ticket("surface2", surface("surface2"))
                packet = harness.compile()
                self.assertEqual(packet["payload"]["state"], "HOLD")
                self.assertEqual(packet["payload"]["counts"]["tickets_held"], 2)

    def test_exact_digest_bound_receipt_yields_no_actionable_commands(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                ticket_path = harness.write_ticket("surface1", surface("surface1"))
                digest = hashlib.sha256(ticket_path.read_bytes()).hexdigest()
                harness.write_receipt(
                    "surface1",
                    receipt("surface1", kind="surface", ticket_sha256=digest),
                )
                packet = harness.compile()
                self.assertEqual(packet["payload"]["state"], "NO_ACTIONABLE_COMMANDS")
                self.assertEqual(packet["payload"]["tickets"][0]["status"], "ALREADY_RECEIPTED")
                self.assertEqual(packet["payload"]["receipts"][0]["status"], "MATCHED_BOUND")

    def test_receipted_action_reminted_under_new_id_is_suppressed(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                first = harness.write_ticket("surface1", surface("surface1"))
                harness.write_ticket("surface2", surface("surface2"))
                harness.write_receipt(
                    "surface1",
                    receipt(
                        "surface1",
                        kind="surface",
                        ticket_sha256=hashlib.sha256(first.read_bytes()).hexdigest(),
                    ),
                )
                packet = harness.compile()
                by_id = {item["id"]: item for item in packet["payload"]["tickets"]}
                self.assertEqual(by_id["surface1"]["status"], "ALREADY_RECEIPTED")
                self.assertEqual(by_id["surface2"]["status"], "HOLD")
                self.assertIn(
                    "ACTION_ALREADY_RECEIPTED",
                    {reason["code"] for reason in by_id["surface2"]["reasons"]},
                )
