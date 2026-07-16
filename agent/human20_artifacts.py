"""Actor-isolated artifacts, deterministic fake delivery, and provider blockers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable


class ArtifactBlocker(RuntimeError):
    """Stable fail-closed artifact error."""


class ProviderBlocker(RuntimeError):
    """Stable fail-closed provider error."""


_ACTOR_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ArtifactWorkspace:
    def __init__(
        self,
        base_root: str | Path,
        actor_id: str | int,
        *,
        max_bytes: int = 25 * 1024 * 1024,
        max_files: int = 128,
    ) -> None:
        actor = str(actor_id)
        if not _ACTOR_RE.fullmatch(actor):
            raise ArtifactBlocker("H20_ARTIFACT_ACTOR_INVALID")
        self.base_root = Path(base_root).expanduser().resolve()
        self.base_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.base_root, 0o700)
        self.root = self.base_root / actor
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.max_bytes = max(1, int(max_bytes))
        self.max_files = max(1, int(max_files))

    def _path(self, relative: str | Path, *, require_exists: bool = False) -> Path:
        rel = Path(relative)
        if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
            raise ArtifactBlocker("H20_ARTIFACT_PATH_DENIED")
        candidate = self.root.joinpath(rel)
        current = self.root
        for part in rel.parts:
            current = current / part
            if current.exists() or current.is_symlink():
                if current.is_symlink():
                    raise ArtifactBlocker("H20_ARTIFACT_SYMLINK_DENIED")
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root.resolve()):
            raise ArtifactBlocker("H20_ARTIFACT_PATH_DENIED")
        if require_exists:
            if not resolved.exists() or not resolved.is_file():
                raise ArtifactBlocker("H20_ARTIFACT_FILE_MISSING")
        return resolved

    def _usage(self) -> tuple[int, int]:
        size = 0
        count = 0
        for path in self.root.rglob("*"):
            if path.is_symlink():
                continue
            if path.is_file():
                size += path.stat().st_size
                count += 1
        return size, count

    @staticmethod
    def _receipt(path: Path) -> dict[str, Any]:
        data = path.read_bytes()
        return {
            "ok": True,
            "path": str(path),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def write_bytes(self, relative: str | Path, data: bytes) -> dict[str, Any]:
        if not isinstance(data, bytes):
            raise ArtifactBlocker("H20_ARTIFACT_BYTES_REQUIRED")
        target = self._path(relative)
        used, count = self._usage()
        old_size = target.stat().st_size if target.exists() and target.is_file() else 0
        if len(data) > self.max_bytes or used - old_size + len(data) > self.max_bytes:
            raise ArtifactBlocker("H20_ARTIFACT_OVERSIZE")
        if not target.exists() and count >= self.max_files:
            raise ArtifactBlocker("H20_ARTIFACT_FILE_QUOTA")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target.parent, 0o700)
        fd, temp_name = tempfile.mkstemp(prefix=".h20-artifact-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return self._receipt(target)

    def read_bytes(self, relative: str | Path) -> bytes:
        return self._path(relative, require_exists=True).read_bytes()

    def patch_text(self, relative: str | Path, old: str, new: str) -> dict[str, Any]:
        path = self._path(relative, require_exists=True)
        text = path.read_text()
        if text.count(old) != 1:
            raise ArtifactBlocker("H20_ARTIFACT_PATCH_NOT_UNIQUE")
        return self.write_bytes(relative, text.replace(old, new, 1).encode())

    def create_archive(self, relative: str | Path, members: Iterable[str | Path]) -> dict[str, Any]:
        target = self._path(relative)
        member_paths = [(str(member), self._path(member, require_exists=True)) for member in members]
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(prefix=".h20-archive-", dir=target.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for member, path in member_paths:
                    archive.write(path, arcname=member)
            data = Path(temp_name).read_bytes()
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return self.write_bytes(relative, data)


class FakeTelegramDelivery:
    """Effect-free Telegram-shaped delivery used before approved canary."""

    def __init__(self, workspace: ArtifactWorkspace) -> None:
        self.workspace = workspace
        self.live_send_count = 0

    def deliver(self, relative: str | Path, *, requested: bool) -> dict[str, Any]:
        if not requested:
            return {
                "ok": False,
                "code": "H20_MEDIA_UNSOLICITED",
                "completed": False,
                "live_send": False,
            }
        try:
            path = self.workspace._path(relative, require_exists=True)
        except ArtifactBlocker as exc:
            code = "H20_DELIVERY_FILE_MISSING" if "FILE_MISSING" in str(exc) else str(exc)
            return {"ok": False, "code": code, "completed": False, "live_send": False}
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        return {
            "ok": True,
            "platform": "telegram",
            "transport": "fake",
            "chat_id": "fake-sandbox",
            "message_id": f"fake-{digest[:16]}",
            "file_name": path.name,
            "file_sha256": digest,
            "size": len(data),
            "live_send": False,
        }


def classify_provider_failure(error: BaseException) -> dict[str, Any]:
    message = str(error).lower()
    if isinstance(error, TimeoutError):
        code = "H20_PROVIDER_TIMEOUT"
    elif isinstance(error, (ConnectionError, OSError)):
        code = "H20_PROVIDER_NETWORK"
    elif "quota" in message or "rate limit" in message or "429" in message:
        code = "H20_PROVIDER_QUOTA"
    else:
        code = "H20_PROVIDER_FAILED"
    return {"ok": False, "code": code, "completed": False, "detail": type(error).__name__}


def enforce_provider_limits(
    *,
    payload_bytes: int,
    max_bytes: int,
    elapsed_seconds: float,
    timeout_seconds: float,
) -> None:
    if payload_bytes > max_bytes:
        raise ProviderBlocker("H20_PROVIDER_OVERSIZE")
    if elapsed_seconds > timeout_seconds:
        raise ProviderBlocker("H20_PROVIDER_TIMEOUT")
