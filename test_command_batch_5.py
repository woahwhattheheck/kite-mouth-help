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
    write_utf8(harness.receipts / "inbox.txt", "reserved receipt directory marker\n")
    _run_git(harness.root, "init", "-q")
    _run_git(harness.root, "config", "user.name", "Command Batch Tests")
    _run_git(harness.root, "config", "user.email", "tests@example.invalid")
    _run_git(harness.root, "add", "COMMANDS")
    _run_git(harness.root, "commit", "-q", "-m", "snapshot")
    return _run_git(harness.root, "rev-parse", "HEAD")


class CommandBatchTests5(unittest.TestCase):
    def test_commit_snapshot_survives_whole_worktree_directory_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = BatchHarness(Path(tmp))
            harness.write_ticket("a", surface("a"))
            harness.write_ticket("b", surface("b"))
            commit_sha = _commit_harness(harness)

            old_commands = harness.root / "old-COMMANDS"
            os.replace(harness.commands, old_commands)
            harness.commands.mkdir()
            harness.receipts.mkdir()
            harness.write_ticket("a", surface("a", claimed_from="KITE"))

            packet_path = harness.root / "batch.json"
            summary_path = harness.root / "batch.md"
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
            packet = json.loads(packet_path.read_bytes())
            payload = packet["payload"]
            self.assertEqual(payload["state"], "HOLD")
            self.assertEqual(payload["counts"]["tickets_total"], 2)
            self.assertEqual(
                {item["path"] for item in payload["source_set"] if item["role"] == "ticket"},
                {"COMMANDS/a.txt", "COMMANDS/b.txt"},
            )
            duplicate_blockers = [
                item for item in payload["blockers"] if item["code"] == "DUPLICATE_ACTION"
            ]
            self.assertEqual(len(duplicate_blockers), 2)
            self.assertTrue(payload["source_ref"].startswith(f"git-sha1:{commit_sha}:tree:"))
            self.assertIn(":manifest:", payload["source_ref"])

    def test_staging_tamper_is_rejected_against_git_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = BatchHarness(Path(tmp))
            harness.write_ticket("say1", say("say1"))
            commit_sha = _commit_harness(harness)
            with cb.frozen_git_snapshot(
                harness.root,
                commit_sha,
                commands_dir=harness.commands,
                receipts_dir=harness.receipts,
            ) as snapshot:
                snapshot.ticket_paths[0].write_bytes(
                    say("say1", body="different staged bytes").encode("utf-8")
                )
                packet = cb.compile_batch(
                    snapshot.ticket_paths,
                    snapshot.receipt_paths,
                    source_ref=snapshot.source_ref,
                    ticket_root=snapshot.ticket_root,
                    receipt_root=snapshot.receipt_root,
                )
                with self.assertRaisesRegex(cb.BatchError, "GIT_SOURCE_SET_MISMATCH"):
                    snapshot.assert_packet_sources(packet)

    def test_cli_rejects_symbolic_or_abbreviated_source_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = BatchHarness(Path(tmp))
            harness.write_ticket("say1", say("say1"))
            _commit_harness(harness)
            for invalid in ("HEAD", "deadbeef", "A" * 40):
                with self.subTest(invalid=invalid):
                    with self.assertRaisesRegex(cb.BatchError, "GIT_COMMIT"):
                        with cb.frozen_git_snapshot(
                            harness.root,
                            invalid,
                            commands_dir=harness.commands,
                            receipts_dir=harness.receipts,
                        ):
                            pass

    def test_git_symlink_ticket_is_rejected_without_materializing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = BatchHarness(Path(tmp))
            harness.write_ticket("say1", say("say1"))
            _commit_harness(harness)
            object_id = _run_git(
                harness.root,
                "hash-object",
                "-w",
                "--stdin",
                input_bytes=b"target-name",
            )
            _run_git(
                harness.root,
                "update-index",
                "--add",
                "--cacheinfo",
                "120000",
                object_id,
                "COMMANDS/evil.txt",
            )
            _run_git(harness.root, "commit", "-q", "-m", "add git symlink ticket")
            commit_sha = _run_git(harness.root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(cb.BatchError, "GIT_INPUT_NOT_REGULAR"):
                with cb.frozen_git_snapshot(
                    harness.root,
                    commit_sha,
                    commands_dir=harness.commands,
                    receipts_dir=harness.receipts,
                ):
                    pass
