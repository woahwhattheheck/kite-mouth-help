from test_command_batch_support import *


class CommandBatchTests3(unittest.TestCase):
    def test_receipt_duplicate_key_and_control_bytes_fail_closed(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket("surface1", surface("surface1"))
                path = harness.write_receipt(
                    "surface1",
                    receipt("surface1", kind="surface") + "kind=surface\n",
                )
                packet = harness.compile()
                codes = {reason["code"] for reason in packet["payload"]["receipts"][0]["reasons"]}
                self.assertIn("RECEIPT_DUPLICATE_KEY", codes)

                path.write_bytes(receipt("surface1", kind="surface").encode() + b"x=bad\x00\n")
                packet = harness.compile()
                codes = {reason["code"] for reason in packet["payload"]["receipts"][0]["reasons"]}
                self.assertIn("RECEIPT_CONTROL", codes)

    def test_ticket_bom_invalid_utf8_oversize_and_nonregular_fail_closed(self):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                commands = root / "COMMANDS"
                receipts = commands / "RECEIPTS"
                commands.mkdir()
                receipts.mkdir()
                cases = [
                    (b"\xef\xbb\xbf" + surface("bad").encode(), "TICKET_BOM"),
                    (surface("bad").encode() + b"\xff", "TICKET_UTF8"),
                    (b"x" * (cb.MAX_INPUT_BYTES + 1), "INPUT_OVERSIZE"),
                ]
                path = commands / "bad.txt"
                for payload, expected in cases:
                    with self.subTest(expected=expected):
                        path.write_bytes(payload)
                        packet = cb.compile_batch(
                            [path], [], source_ref="x", ticket_root=commands, receipt_root=receipts
                        )
                        codes = {reason["code"] for reason in packet["payload"]["tickets"][0]["reasons"]}
                        self.assertIn(expected, codes)
                path.unlink()
                path.mkdir()
                packet = cb.compile_batch([path], [], source_ref="x", ticket_root=commands)
                codes = {reason["code"] for reason in packet["payload"]["tickets"][0]["reasons"]}
                self.assertIn("INPUT_NOT_REGULAR", codes)

    def test_final_symlink_input_fails_closed(self):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / "real.txt"
                write_utf8(target, surface("link"))
                link = root / "link.txt"
                try:
                    link.symlink_to(target)
                except (OSError, NotImplementedError):
                    self.skipTest("symlink unavailable")
                packet = cb.compile_batch([link], [], source_ref="x", ticket_root=root)
                codes = {reason["code"] for reason in packet["payload"]["tickets"][0]["reasons"]}
                self.assertIn("INPUT_SYMLINK", codes)

    def test_input_order_does_not_change_packet_or_summary(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                first = harness.write_ticket("a", say("a", body="alpha"))
                second = harness.write_ticket("b", say("b", body="beta"))
                one = cb.compile_batch(
                    [first, second], [], source_ref="x", ticket_root=harness.commands
                )
                two = cb.compile_batch(
                    [second, first], [], source_ref="x", ticket_root=harness.commands
                )
                self.assertEqual(cb.packet_bytes(one), cb.packet_bytes(two))
                self.assertEqual(cb.render_summary(one), cb.render_summary(two))

    def test_packet_and_source_tamper_are_rejected(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                path = harness.write_ticket("surface1", surface("surface1"))
                packet = harness.compile(source_ref="x")
                data = cb.packet_bytes(packet)
                cb.verify_packet(
                    data,
                    [path],
                    [],
                    source_ref="x",
                    ticket_root=harness.commands,
                    receipt_root=harness.receipts,
                )

                tampered = json.loads(data)
                tampered["payload"]["state"] = "NO_ACTIONABLE_COMMANDS"
                tampered_data = cb._canonical_json_bytes(tampered, newline=True)
                with self.assertRaisesRegex(cb.BatchError, "PACKET_DIGEST"):
                    cb.verify_packet(
                        tampered_data,
                        [path],
                        [],
                        source_ref="x",
                        ticket_root=harness.commands,
                        receipt_root=harness.receipts,
                    )

                write_utf8(path, surface("surface1", extra="note=changed\n"))
                with self.assertRaisesRegex(cb.BatchError, "PACKET_DRIFT"):
                    cb.verify_packet(
                        data,
                        [path],
                        [],
                        source_ref="x",
                        ticket_root=harness.commands,
                        receipt_root=harness.receipts,
                    )

    def test_noncanonical_and_duplicate_key_packets_are_rejected(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                packet = harness.compile(source_ref="x")
                pretty = json.dumps(packet, indent=2, sort_keys=True).encode() + b"\n"
                with self.assertRaisesRegex(cb.BatchError, "PACKET_CANONICAL"):
                    cb.parse_packet_bytes(pretty)
                duplicate = b'{"schema":"kite-mouth.command-batch/v1","schema":"x","payload":{},"payload_sha256":"' + b"0" * 64 + b'"}\n'
                with self.assertRaisesRegex(cb.BatchError, "PACKET_DUPLICATE_KEY"):
                    cb.parse_packet_bytes(duplicate)

    def test_summary_tamper_is_rejected(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                path = harness.write_ticket("surface1", surface("surface1"))
                packet = harness.compile(source_ref="x")
                with self.assertRaisesRegex(cb.BatchError, "SUMMARY_DRIFT"):
                    cb.verify_packet(
                        cb.packet_bytes(packet),
                        [path],
                        [],
                        source_ref="x",
                        ticket_root=harness.commands,
                        receipt_root=harness.receipts,
                        summary=b"tampered\n",
                    )

    def test_create_exclusive_output_refuses_existing_and_symlink(self):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                existing = root / "existing.json"
                existing.write_bytes(b"old")
                with self.assertRaisesRegex(cb.BatchError, "OUTPUT_EXISTS"):
                    cb._preflight_new_output(existing)
                with self.assertRaisesRegex(cb.BatchError, "OUTPUT_CREATE_ERROR"):
                    cb.write_new_file(existing, b"new")
                self.assertEqual(existing.read_bytes(), b"old")

                target = root / "target.json"
                target.write_bytes(b"target")
                link = root / "link.json"
                try:
                    link.symlink_to(target)
                except (OSError, NotImplementedError):
                    return
                with self.assertRaisesRegex(cb.BatchError, "OUTPUT_EXISTS"):
                    cb._preflight_new_output(link)
                self.assertEqual(target.read_bytes(), b"target")
