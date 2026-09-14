import subprocess

from test_command_batch_support import *


def _run_git(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.decode("ascii").strip()


def _commit_harness(harness: BatchHarness) -> str:
    # Git does not track empty directories. This reserved receipt keeps the
    # receipt tree present without entering the selected source set.
    write_utf8(harness.receipts / "inbox.txt", "reserved receipt directory marker\n")
    _run_git(harness.root, "init", "-q")
    _run_git(harness.root, "config", "user.name", "Command Batch Tests")
    _run_git(harness.root, "config", "user.email", "tests@example.invalid")
    _run_git(harness.root, "add", "COMMANDS")
    _run_git(harness.root, "commit", "-q", "-m", "snapshot")
    return _run_git(harness.root, "rev-parse", "HEAD")


class CommandBatchTests4(unittest.TestCase):
    def test_failed_output_write_leaves_partial_for_manual_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "partial.json"
            with mock.patch.object(cb.os, "write", return_value=0):
                with self.assertRaisesRegex(cb.BatchError, "OUTPUT_WRITE_ERROR"):
                    cb.write_new_file(path, b"payload")
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"")

    def test_failed_output_write_never_unlinks_foreign_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "partial.json"
            displaced = root / "owned-partial.json"
            real_close = cb.os.close
            real_open = cb.os.open
            real_write = cb.os.write
            swapped = False

            def replacing_close(fd):
                nonlocal swapped
                real_close(fd)
                if not swapped:
                    os.replace(path, displaced)
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                    replacement_fd = real_open(path, flags, 0o600)
                    try:
                        real_write(replacement_fd, b"foreign")
                    finally:
                        real_close(replacement_fd)
                    swapped = True

            with mock.patch.object(cb.os, "write", return_value=0):
                with mock.patch.object(cb.os, "close", side_effect=replacing_close):
                    with self.assertRaisesRegex(cb.BatchError, "OUTPUT_WRITE_ERROR"):
                        cb.write_new_file(path, b"payload")
            self.assertTrue(swapped)
            self.assertEqual(path.read_bytes(), b"foreign")
            self.assertEqual(displaced.read_bytes(), b"")

    def test_cli_compile_verify_round_trip_uses_exact_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = BatchHarness(Path(tmp))
            ticket_path = harness.write_ticket("say1", say("say1"))
            commit_sha = _commit_harness(harness)
            packet_path = harness.root / "batch.json"
            summary_path = harness.root / "batch.md"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = cb.main(
                    [
                        "compile",
                        "--git-repo",
                        str(harness.root),
                        "--commands-dir",
                        str(harness.commands),
                        "--receipts-dir",
                        str(harness.receipts),
                        "--source-ref",
                        commit_sha,
                        "--packet-out",
                        str(packet_path),
                        "--summary-out",
                        str(summary_path),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertIn("COMPILED: READY_FOR_HOST_BATCH_REVIEW", stdout.getvalue())

            # Verification must continue to use the exact commit even after the
            # mutable worktree has different bytes under the same lexical path.
            write_utf8(ticket_path, say("say1", body="mutated worktree body"))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = cb.main(
                    [
                        "verify",
                        "--git-repo",
                        str(harness.root),
                        "--commands-dir",
                        str(harness.commands),
                        "--receipts-dir",
                        str(harness.receipts),
                        "--source-ref",
                        commit_sha,
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
            commit_sha = _commit_harness(harness)
            result = cb.main(
                [
                    "compile",
                    "--git-repo",
                    str(harness.root),
                    "--commands-dir",
                    str(harness.commands),
                    "--receipts-dir",
                    str(harness.receipts),
                    "--source-ref",
                    commit_sha,
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
