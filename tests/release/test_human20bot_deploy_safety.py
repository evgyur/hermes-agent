from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from deploy_human20bot_team_access import (
    DeploymentError,
    _acquire_deployment_lock,
    _consume_approval_scope,
)


def test_deployment_lock_rejects_concurrent_writer(tmp_path: Path) -> None:
    lock_path = tmp_path / "global.lock"
    first = _acquire_deployment_lock(lock_path)
    try:
        with pytest.raises(DeploymentError, match="another deployment writer"):
            _acquire_deployment_lock(lock_path)
    finally:
        fcntl.flock(first, fcntl.LOCK_UN)
        os.close(first)


def test_approval_scope_is_consumed_once_and_private(tmp_path: Path) -> None:
    root = tmp_path / "global-state"
    scope = "a" * 64
    path = _consume_approval_scope(root, scope, "b" * 40)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(DeploymentError, match="already consumed"):
        _consume_approval_scope(root, scope, "b" * 40)


def test_consumed_scope_is_readable_by_explicit_runtime_owner(tmp_path: Path) -> None:
    root = tmp_path / "runtime-owned-state"
    path = _consume_approval_scope(
        root, "e" * 64, "f" * 40,
        owner_uid=os.getuid(), owner_gid=os.getgid(),
    )
    approvals = root / "approval-consumption"
    assert (root.stat().st_uid, root.stat().st_gid) == (os.getuid(), os.getgid())
    assert (approvals.stat().st_uid, approvals.stat().st_gid) == (os.getuid(), os.getgid())
    assert (path.stat().st_uid, path.stat().st_gid) == (os.getuid(), os.getgid())
    assert path.read_text(encoding="utf-8")


def test_consumption_rejects_nondirectory_with_bounded_error(tmp_path: Path) -> None:
    root = tmp_path / "unsafe-state"
    root.mkdir()
    (root / "approval-consumption").write_text("not a directory", encoding="utf-8")
    with pytest.raises(DeploymentError, match="private real approvals directory required"):
        _consume_approval_scope(root, "9" * 64, "8" * 40)


def test_two_artifact_roots_share_one_global_lock_and_consumption(tmp_path: Path) -> None:
    (tmp_path / "artifact-a").mkdir()
    (tmp_path / "artifact-b").mkdir()
    global_lock = tmp_path / "human20bot.lock"
    global_state = tmp_path / "human20bot-state"
    fd = _acquire_deployment_lock(global_lock)
    try:
        with pytest.raises(DeploymentError, match="another deployment writer"):
            _acquire_deployment_lock(global_lock)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    _consume_approval_scope(global_state, "c" * 64, "d" * 40)
    with pytest.raises(DeploymentError, match="already consumed"):
        _consume_approval_scope(global_state, "c" * 64, "d" * 40)
