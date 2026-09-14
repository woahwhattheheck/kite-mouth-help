from test_command_batch_support import *


class CommandBatchTests4(unittest.TestCase):
    def test_failed_output_write_removes_only_its_own_partial_file(self):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "partial.json"
                with mock.patch.object(cb.os, "write", return_value=0):
                    with self.assertRaisesRegex(cb.BatchError, "OUTPUT_WRITE_ERROR"):
                        cb.write_new_file(path, b"payload")
                self.assertFalse(path.exists())

    def test_cli_compile_verify_round_trip(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket("say1", say("say1"))
                packet_path = harness.root / "batch.json"
                summary_path = harness.root / "batch.md"
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    result = cb.main(
                        [
                            "compile",
                            "--commands-dir",
                            str(harness.commands),
                            "--receipts-dir",
                            str(harness.receipts),
                            "--source-ref",
                            "head-123",
                            "--packet-out",
                            str(packet_path),
                            "--summary-out",
                            str(summary_path),
                        ]
                    )
                self.assertEqual(result, 0)
                self.assertIn("COMPILED: READY_FOR_HOST_BATCH_REVIEW", stdout.getvalue())
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    result = cb.main(
                        [
                            "verify",
                            "--commands-dir",
                            str(harness.commands),
                            "--receipts-dir",
                            str(harness.receipts),
                            "--source-ref",
                            "head-123",
                            "--packet",
                            str(packet_path),
                            "--summary",
                            str(summary_path),
                        ]
                    )
                self.assertEqual(result, 0)
                self.assertIn("VERIFIED: READY_FOR_HOST_BATCH_REVIEW", stdout.getvalue())

    def test_cli_fail_on_hold_is_distinct_from_compile_failure(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket("a", surface("a"))
                harness.write_ticket("b", surface("b"))
                result = cb.main(
                    [
                        "compile",
                        "--commands-dir",
                        str(harness.commands),
                        "--receipts-dir",
                        str(harness.receipts),
                        "--source-ref",
                        "x",
                        "--packet-out",
                        str(harness.root / "batch.json"),
                        "--summary-out",
                        str(harness.root / "batch.md"),
                        "--fail-on-hold",
                    ]
                )
                self.assertEqual(result, 3)

    def test_directory_freeze_includes_txt_directory_for_fail_closed_read(self):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fake = root / "fake.txt"
                fake.mkdir()
                paths = cb.freeze_directory(root, reserved_names=frozenset())
                self.assertEqual(paths, [fake])
                packet = cb.compile_batch(paths, [], source_ref="x", ticket_root=root)
                codes = {reason["code"] for reason in packet["payload"]["tickets"][0]["reasons"]}
                self.assertIn("INPUT_NOT_REGULAR", codes)

    def test_path_replacement_between_lstat_and_open_is_rejected(self):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / "surface1.txt"
                replacement = root / "replacement.tmp"
                write_utf8(path, surface("surface1"))
                write_utf8(replacement, surface("surface1", claimed_from="KITE"))
                real_open = os.open
                swapped = False

                def replacing_open(open_path, flags, *args, **kwargs):
                    nonlocal swapped
                    if not swapped and Path(open_path) == path:
                        os.replace(replacement, path)
                        swapped = True
                    return real_open(open_path, flags, *args, **kwargs)

                with mock.patch.object(cb.os, "open", side_effect=replacing_open):
                    with self.assertRaisesRegex(cb.BatchError, "INPUT_REPLACED"):
                        cb.read_bounded_regular(path, label="COMMANDS/surface1.txt", role="ticket")

    def test_receipt_filename_id_mismatch_is_reported(self):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / "wrong.txt"
                write_utf8(path, receipt("right", kind="surface"))
                packet = cb.compile_batch([], [path], source_ref="x", receipt_root=root)
                codes = {reason["code"] for reason in packet["payload"]["receipts"][0]["reasons"]}
                self.assertIn("RECEIPT_FILENAME_ID_MISMATCH", codes)

    def test_source_ref_rejects_controls(self):
            with self.assertRaisesRegex(cb.BatchError, "SOURCE_REF"):
                cb.compile_batch([], [], source_ref="bad\nref")

    def test_markdown_is_deterministic_and_carries_non_authority_warning(self):
            with tempfile.TemporaryDirectory() as tmp:
                harness = BatchHarness(Path(tmp))
                harness.write_ticket("say1", say("say1"))
                packet = harness.compile(source_ref="x")
                summary = cb.render_summary(packet)
                self.assertEqual(summary, cb.render_summary(packet))
                self.assertIn("does not execute or authorize any command", summary)
                self.assertIn(packet["payload_sha256"], summary)
