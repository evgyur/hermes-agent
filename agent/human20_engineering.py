"""Admin-only isolated engineering lane for Human20 beta candidates."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Sequence


class EngineeringBlocker(RuntimeError):
    pass


class EngineeringLane:
    _ALLOWED_TEST_EXECUTABLES = {"python", "python3", "pytest", "py.test"}
    _BYPASS_EXECUTABLES = {"docker", "sudo", "bash", "sh", "terminal", "human20team-ops"}

    def authorize_test_command(self, actor_role: str, argv: Sequence[str]) -> dict[str, object]:
        if actor_role != "admin":
            return {"allowed": False, "code": "H20_ENGINEERING_ADMIN_REQUIRED"}
        if not argv:
            return {"allowed": False, "code": "H20_ENGINEERING_PROCESS_BYPASS"}
        executable = Path(str(argv[0])).name
        if executable in self._BYPASS_EXECUTABLES or executable not in self._ALLOWED_TEST_EXECUTABLES:
            return {"allowed": False, "code": "H20_ENGINEERING_PROCESS_BYPASS"}
        if executable in {"python", "python3"}:
            if len(argv) < 3 or argv[1] != "-m" or argv[2] not in {"py_compile", "pytest"}:
                return {"allowed": False, "code": "H20_ENGINEERING_PROCESS_BYPASS"}
        if any(str(token) in {"-c", "--pdb", "--trace"} for token in argv[1:]):
            return {"allowed": False, "code": "H20_ENGINEERING_PROCESS_BYPASS"}
        return {"allowed": True, "code": "H20_ENGINEERING_TEST_ALLOWED"}

    @staticmethod
    def _safe_candidate_path(worktree: Path, relative: str) -> Path:
        supplied = Path(relative)
        if supplied.is_absolute() or ".." in supplied.parts:
            raise EngineeringBlocker("H20_ENGINEERING_PATH_DENIED")
        destination = worktree / supplied
        if destination.is_symlink():
            raise EngineeringBlocker("H20_ENGINEERING_PATH_DENIED")
        for parent in destination.parents:
            if parent == worktree.parent:
                break
            if parent.is_symlink():
                raise EngineeringBlocker("H20_ENGINEERING_PATH_DENIED")
        resolved_parent = destination.parent.resolve()
        if not resolved_parent.is_relative_to(worktree.resolve()):
            raise EngineeringBlocker("H20_ENGINEERING_PATH_DENIED")
        return destination

    def run_beta_candidate(
        self,
        *,
        actor_role: str,
        repo: Path,
        changes: Mapping[str, str],
        test_argv: Sequence[str],
    ) -> dict[str, object]:
        if actor_role != "admin":
            raise EngineeringBlocker("H20_ENGINEERING_ADMIN_REQUIRED")
        decision = self.authorize_test_command(actor_role, test_argv)
        if not decision["allowed"]:
            raise EngineeringBlocker(str(decision["code"]))
        repo = Path(repo).resolve()
        if not (repo / ".git").exists():
            raise EngineeringBlocker("H20_ENGINEERING_REPO_INVALID")
        baseline_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        baseline_status = subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).splitlines()
        if baseline_status:
            raise EngineeringBlocker("H20_ENGINEERING_LIVE_TREE_DIRTY")
        worktree = Path(tempfile.mkdtemp(prefix="h20-beta-worktree-"))
        readback: dict[str, str] = {}
        try:
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), baseline_sha], check=True, capture_output=True, text=True)
            for relative, content in changes.items():
                destination = self._safe_candidate_path(worktree, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content)
                os.chmod(destination, 0o600)
            test = subprocess.run(list(test_argv), cwd=worktree, capture_output=True, text=True, timeout=120)
            if test.returncode != 0:
                raise EngineeringBlocker("H20_ENGINEERING_TESTS_FAILED")
            subprocess.run(["git", "-C", str(worktree), "add", "--all"], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(worktree), "commit", "-m", "Human20 beta candidate"], check=True, capture_output=True, text=True)
            candidate_sha = subprocess.check_output(["git", "-C", str(worktree), "rev-parse", "HEAD"], text=True).strip()
            for relative in changes:
                readback[relative] = self._safe_candidate_path(worktree, relative).read_text()
            if subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() != baseline_sha:
                raise EngineeringBlocker("H20_ENGINEERING_LIVE_TREE_CHANGED")
            return {
                "ok": True,
                "environment": "beta",
                "baseline_sha": baseline_sha,
                "candidate_sha": candidate_sha,
                "tests": {"returncode": test.returncode, "stdout": test.stdout[-2000:]},
                "readback": readback,
                "production_mutations": 0,
                "pr_mode": "proposal_only",
            }
        finally:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)], capture_output=True, text=True)
            shutil.rmtree(worktree, ignore_errors=True)
