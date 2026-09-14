#!/usr/bin/env python3
"""Run the public CI pilot with a hard wall-clock bound and a JSON receipt."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 300
TEST_COMMAND = (
    sys.executable,
    "-m",
    "unittest",
    "-v",
    "test_validate_commands.py",
    "ci.test_circleci_contract",
)
HASHED_PATHS = (
    Path("validate_commands.py"),
    Path("test_validate_commands.py"),
    Path(".circleci/config.yml"),
    Path("ci/run_public_ci_pilot.py"),
    Path("ci/test_circleci_contract.py"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main() -> int:
    missing = [str(path) for path in HASHED_PATHS if not (ROOT / path).is_file()]
    if missing:
        print("CI_PILOT_ERROR=" + json.dumps({"missing_files": missing}, sort_keys=True), file=sys.stderr)
        return 2

    started = time.monotonic()
    process = subprocess.Popen(
        TEST_COMMAND,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)
        output, _ = process.communicate()

    if output:
        print(output, end="" if output.endswith("\n") else "\n")

    elapsed_ms = round((time.monotonic() - started) * 1000)
    exit_code = 124 if timed_out else int(process.returncode or 0)
    receipt = {
        "schema": "kite-mouth-help.circleci-public-pilot.v1",
        "status": "timeout" if timed_out else ("passed" if exit_code == 0 else "failed"),
        "exit_code": exit_code,
        "elapsed_ms": elapsed_ms,
        "timeout_seconds": TIMEOUT_SECONDS,
        "command": list(TEST_COMMAND),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "circle_sha1": os.environ.get("CIRCLE_SHA1", ""),
        "files": {str(path): _sha256(ROOT / path) for path in HASHED_PATHS},
    }
    print("CI_PILOT_RECEIPT=" + json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
