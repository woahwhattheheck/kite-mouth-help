from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".circleci" / "config.yml"
RUNNER_PATH = ROOT / "ci" / "run_public_ci_pilot.py"


class CircleCIPilotContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = CONFIG_PATH.read_text(encoding="utf-8")
        cls.runner = RUNNER_PATH.read_text(encoding="utf-8")
        cls.runner_tree = ast.parse(cls.runner, filename=str(RUNNER_PATH))

    def test_workflow_is_explicitly_default_off(self):
        self.assertRegex(
            self.config,
            r"(?ms)^parameters:\n  run_public_ci_pilot:\n    type: boolean\n    default: false$",
        )
        self.assertIn("when: << pipeline.parameters.run_public_ci_pilot >>", self.config)
        self.assertNotIn("triggers:", self.config)
        self.assertNotIn("schedule:", self.config)

    def test_executor_and_parallelism_are_bounded(self):
        self.assertEqual(self.config.count("resource_class:"), 1)
        self.assertIn("resource_class: small", self.config)
        self.assertNotRegex(self.config, r"resource_class:\s*(medium|large|xlarge|2xlarge)")
        self.assertEqual(self.config.count("parallelism:"), 1)
        self.assertIn("parallelism: 1", self.config)
        self.assertEqual(self.config.count("- image:"), 1)
        self.assertIn("image: cimg/python:3.12.14", self.config)

    def test_config_has_one_bounded_test_step(self):
        self.assertEqual(self.config.count("command:"), 1)
        self.assertIn("command: python ci/run_public_ci_pilot.py", self.config)
        self.assertIn("no_output_timeout: 6m", self.config)
        self.assertEqual(self.config.count("- checkout"), 1)

    def test_config_has_no_cost_or_secret_amplifiers(self):
        forbidden = (
            "orbs:",
            "context:",
            "machine:",
            "macos:",
            "setup_remote_docker",
            "save_cache",
            "restore_cache",
            "persist_to_workspace",
            "attach_workspace",
            "store_artifacts",
            "store_test_results",
            "pip install",
            "apt-get",
            "curl ",
            "wget ",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.config)

    def test_runner_timeout_and_test_command_are_fixed(self):
        assignments = {
            node.targets[0].id: node.value
            for node in self.runner_tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }
        timeout = ast.literal_eval(assignments["TIMEOUT_SECONDS"])
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 300)
        command = assignments["TEST_COMMAND"]
        self.assertIsInstance(command, ast.Tuple)
        self.assertIsInstance(command.elts[0], ast.Attribute)
        self.assertIsInstance(command.elts[0].value, ast.Name)
        self.assertEqual(command.elts[0].value.id, "sys")
        self.assertEqual(command.elts[0].attr, "executable")
        command_tail = tuple(ast.literal_eval(element) for element in command.elts[1:])
        self.assertEqual(
            command_tail,
            (
                "-m",
                "unittest",
                "-v",
                "test_validate_commands.py",
                "test_validate_commands_ancestors.py",
                "ci.test_circleci_contract",
            ),
        )
        self.assertNotIn("shell=True", self.runner)

    def test_runner_emits_hash_bound_receipt(self):
        for path in (
            "validate_commands.py",
            "validate_commands_core.py",
            "test_validate_commands.py",
            "test_validate_commands_ancestors.py",
            ".circleci/config.yml",
            "ci/run_public_ci_pilot.py",
            "ci/test_circleci_contract.py",
        ):
            self.assertIn(path, self.runner)
        self.assertIn("CI_PILOT_RECEIPT=", self.runner)
        self.assertIn("circle_sha1", self.runner)
        self.assertRegex(self.runner, r"TIMEOUT_SECONDS\s*=\s*300")


if __name__ == "__main__":
    unittest.main()
