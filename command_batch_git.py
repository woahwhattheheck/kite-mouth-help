"""Local-only exact-Git source loader.

The implementation is preserved byte-for-byte in ``command_batch_git_impl``.
This public loader hardens every implementation Git invocation by forcing Git's
promisor/lazy-fetch kill switch into the deterministic child environment before
any exported loader function can run.
"""
from __future__ import annotations

import sys

import command_batch_git_impl as _impl

_original_git_environment = _impl._git_environment


def _local_only_git_environment() -> dict[str, str]:
    env = _original_git_environment()
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


_impl._git_environment = _local_only_git_environment
# Preserve one module identity for monkeypatching/hostile tests: callers that
# import command_batch_git receive the hardened implementation module itself.
sys.modules[__name__] = _impl
