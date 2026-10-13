import subprocess

from test_command_batch_support import *


def _run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
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


class CommandBatchPublicAuthorityTests(unittest.TestCase):
    def test_public_compile_derives_complete_committed_source_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = BatchHarness(Path(tmp))
            harness.write_ticket("a", surface("a"))
            harness.write_ticket("b", surface("b"))
            commit_sha = _commit_harness(harness)

            packet = PUBLIC_COMPILE_BATCH(
                git_repo=harness.root,
                source_ref=commit_sha,
                commands_dir=harness.commands,
                receipts_dir=harness.receipts,
            )
            self.assertEqual(packet["schema"], cb.SCHEMA)
            self.assertEqual(packet["payload"]["state"], "HOLD")
            self.assertEqual(packet["payload"]["counts"]["tickets_total"], 2)
            self.assertEqual(
                {item["path"] for item in packet["payload"]["source_set"] if item["role"] == "ticket"},
                {"COMMANDS/a.txt", "COMMANDS/b.txt"},
            )

    def test_public_compile_has_no_path_subset_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = BatchHarness(Path(tmp))
            ticket = harness.write_ticket("a", surface("a"))
            commit_sha = _commit_harness(harness)
            with self.assertRaises(TypeError):
                PUBLIC_COMPILE_BATCH(
                    [ticket],
                    [],
                    source_ref=commit_sha,
                    git_repo=harness.root,
                )

    def test_public_verify_rejects_ready_packet_minted_from_omitted_commit_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = BatchHarness(Path(tmp))
            harness.write_ticket("a", surface("a"))
            harness.write_ticket("b", surface("b"))
            commit_sha = _commit_harness(harness)

            with cb.frozen_git_snapshot(
                harness.root,
                commit_sha,
                commands_dir=harness.commands,
                receipts_dir=harness.receipts,
            ) as snapshot:
                raw_subset = cb.compile_batch(
                    [snapshot.ticket_paths[0]],
                    [],
                    source_ref=snapshot.source_ref,
                    ticket_root=snapshot.ticket_root,
                    receipt_root=snapshot.receipt_root,
                )
                self.assertEqual(raw_subset["schema"], cb.SCHEMA)
                self.assertEqual(raw_subset["payload"]["state"], "READY_FOR_HOST_BATCH_REVIEW")
                self.assertEqual(raw_subset["payload"]["counts"]["tickets_total"], 1)

            with self.assertRaisesRegex(cb.BatchError, "PACKET_DRIFT"):
                PUBLIC_VERIFY_PACKET(
                    cb.packet_bytes(raw_subset),
                    git_repo=harness.root,
                    source_ref=commit_sha,
                    commands_dir=harness.commands,
                    receipts_dir=harness.receipts,
                )

    def test_public_verify_accepts_exact_public_compile_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = BatchHarness(Path(tmp))
            harness.write_ticket("say1", say("say1"))
            commit_sha = _commit_harness(harness)
            packet = PUBLIC_COMPILE_BATCH(
                git_repo=harness.root,
                source_ref=commit_sha,
                commands_dir=harness.commands,
                receipts_dir=harness.receipts,
            )
            verified = PUBLIC_VERIFY_PACKET(
                cb.packet_bytes(packet),
                git_repo=harness.root,
                source_ref=commit_sha,
                commands_dir=harness.commands,
                receipts_dir=harness.receipts,
                summary=cb.render_summary(packet).encode("utf-8"),
            )
            self.assertEqual(verified, packet)
