import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import command_batch as cb
import command_batch_git as git_loader
from test_command_batch_support import PUBLIC_COMPILE_BATCH


def _git(repository: Path | None, *args: str, env: dict[str, str] | None = None, check: bool = True):
    command = ["git"]
    if repository is not None:
        command += ["-C", str(repository)]
    command += list(args)
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=check,
    )


class CommandBatchNoLazyFetchTests(unittest.TestCase):
    def test_public_loader_child_environment_disables_lazy_fetch(self):
        self.assertEqual(git_loader._git_environment()["GIT_NO_LAZY_FETCH"], "1")

    def test_missing_promisor_blob_fails_without_materializing_then_plain_git_can_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            origin = root / "origin"
            clone = root / "partial"
            origin.mkdir()
            _git(origin, "init", "-q")
            _git(origin, "config", "user.name", "Command Batch Promisor Test")
            _git(origin, "config", "user.email", "tests@example.invalid")
            _git(origin, "config", "uploadpack.allowFilter", "true")
            commands = origin / "COMMANDS"
            receipts = commands / "RECEIPTS"
            receipts.mkdir(parents=True)
            (commands / "a.txt").write_text(
                "id=a\nkind=surface\napproved=YES\nclaimed_from=GROK\nauthenticated_player=UNKNOWN\n",
                encoding="utf-8",
            )
            # Git does not retain empty directories; this reserved marker keeps
            # the selected receipt tree present without becoming a source row.
            (receipts / "inbox.txt").write_text("reserved\n", encoding="utf-8")
            _git(origin, "add", "COMMANDS")
            _git(origin, "commit", "-q", "-m", "promisor fixture")
            commit_sha = _git(origin, "rev-parse", "HEAD").stdout.decode("ascii").strip()
            tree_row = _git(origin, "ls-tree", f"{commit_sha}:COMMANDS", "a.txt").stdout.decode("ascii").strip()
            blob_sha = tree_row.split()[2]

            _git(None, "clone", "-q", "--filter=blob:none", "--no-checkout", origin.resolve().as_uri(), str(clone))

            no_lazy = os.environ.copy()
            no_lazy["GIT_NO_LAZY_FETCH"] = "1"
            before = _git(clone, "cat-file", "-e", blob_sha, env=no_lazy, check=False)
            self.assertNotEqual(before.returncode, 0, "fixture unexpectedly materialized selected blob")

            with self.assertRaisesRegex(cb.BatchError, "GIT_COMMAND_FAILED"):
                PUBLIC_COMPILE_BATCH(
                    git_repo=clone,
                    source_ref=commit_sha,
                    commands_dir=Path("COMMANDS"),
                    receipts_dir=Path("COMMANDS/RECEIPTS"),
                )

            after = _git(clone, "cat-file", "-e", blob_sha, env=no_lazy, check=False)
            self.assertNotEqual(after.returncode, 0, "loader lazily materialized a promised blob")

            ordinary = os.environ.copy()
            ordinary.pop("GIT_NO_LAZY_FETCH", None)
            fetchable = _git(clone, "cat-file", "-e", blob_sha, env=ordinary, check=False)
            self.assertEqual(
                fetchable.returncode,
                0,
                fetchable.stderr.decode("utf-8", errors="replace"),
            )
            now_local = _git(clone, "cat-file", "-e", blob_sha, env=no_lazy, check=False)
            self.assertEqual(now_local.returncode, 0)


if __name__ == "__main__":
    unittest.main()
