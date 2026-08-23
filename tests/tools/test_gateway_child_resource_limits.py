from __future__ import annotations

import os
import resource
import subprocess
import sys

from tools.environments.local import gateway_tool_subprocess_kwargs


def test_gateway_tool_spawn_closes_all_unpassed_fds(monkeypatch):
    monkeypatch.setenv("_HERMES_GATEWAY", "1")

    kwargs = gateway_tool_subprocess_kwargs()

    assert kwargs["close_fds"] is True
    assert kwargs["pass_fds"] == ()
    assert callable(kwargs["preexec_fn"])


def test_gateway_tool_child_nofile_is_bounded_without_lowering_parent(monkeypatch):
    monkeypatch.setenv("_HERMES_GATEWAY", "1")
    parent_limit = resource.getrlimit(resource.RLIMIT_NOFILE)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import resource; print(resource.getrlimit(resource.RLIMIT_NOFILE)[0])",
        ],
        check=True,
        capture_output=True,
        text=True,
        **gateway_tool_subprocess_kwargs(),
    )

    assert int(result.stdout.strip()) <= 4096
    assert resource.getrlimit(resource.RLIMIT_NOFILE) == parent_limit


def test_non_gateway_admin_child_is_not_resource_limited(monkeypatch):
    monkeypatch.delenv("_HERMES_GATEWAY", raising=False)

    kwargs = gateway_tool_subprocess_kwargs()

    assert kwargs == {"close_fds": True, "pass_fds": ()}