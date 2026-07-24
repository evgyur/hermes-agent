#!/usr/bin/env python3
"""Shared fail-closed primitives for the staged P05 tooling."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import subprocess
from pathlib import Path
from typing import Any, Iterable


class SafetyError(RuntimeError):
    """A release-safety invariant failed."""


def _reject_constant(value: str) -> None:
    raise SafetyError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SafetyError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def read_nofollow(path: Path | str, *, max_bytes: int = 16 * 1024 * 1024) -> bytes:
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SafetyError(f"cannot safely open {path}: {exc.strerror}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SafetyError(f"regular file required: {path}")
        if info.st_size > max_bytes:
            raise SafetyError(f"file exceeds safety limit: {path}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise SafetyError(f"file exceeds safety limit: {path}")
        return data
    finally:
        os.close(fd)


def strict_json_load(path: Path | str) -> dict[str, Any]:
    try:
        text = read_nofollow(path).decode("utf-8", "strict")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafetyError(f"invalid strict JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SafetyError(f"JSON object required: {path}")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
                separators=(",", ": "),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SafetyError("value is not strict JSON serializable") from exc


def atomic_write_bytes(path: Path | str, content: bytes, *, mode: int = 0o600) -> None:
    path = Path(path)
    if not path.name or path.name in {".", ".."}:
        raise SafetyError("invalid output filename")
    parent = path.parent
    try:
        parent_info = parent.lstat()
    except OSError as exc:
        raise SafetyError(f"output parent is unavailable: {parent}") from exc
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise SafetyError(f"safe output directory required: {parent}")
    try:
        current = path.lstat()
    except FileNotFoundError:
        current = None
    if current is not None and stat.S_ISLNK(current.st_mode):
        raise SafetyError(f"output symlink is forbidden: {path}")
    if current is not None and not stat.S_ISREG(current.st_mode):
        raise SafetyError(f"regular output file required: {path}")

    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    dir_fd = os.open(parent, dir_flags)
    tmp_name = f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
    fd: int | None = None
    try:
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=dir_fd,
        )
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, mode)
        os.close(fd)
        fd = None
        os.rename(tmp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.chmod(path.name, mode, dir_fd=dir_fd, follow_symlinks=False)
        os.fsync(dir_fd)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        os.close(dir_fd)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    return sha256_bytes(read_nofollow(path, max_bytes=1024 * 1024 * 1024))


def ensure_directory(path: Path | str, *, mode: int = 0o700, create: bool = False) -> Path:
    path = Path(path)
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=mode)
    try:
        info = path.lstat()
    except OSError as exc:
        raise SafetyError(f"directory is unavailable: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SafetyError(f"real directory required: {path}")
    os.chmod(path, mode)
    return path


def confined_path(root: Path | str, relative: str) -> Path:
    root = Path(root).resolve(strict=True)
    candidate = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise SafetyError(f"unsafe relative path: {relative}")
    resolved_parent = candidate.parent.resolve(strict=True)
    if resolved_parent != root and root not in resolved_parent.parents:
        raise SafetyError(f"path escapes root: {relative}")
    return candidate


def hash_tree(root: Path | str, *, require_private: bool = False) -> dict[str, Any]:
    root = Path(root)
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SafetyError(f"real directory required: {root}")
    if require_private and stat.S_IMODE(info.st_mode) != 0o700:
        raise SafetyError(f"directory mode must be 0700: {root}")
    entries: list[dict[str, Any]] = []
    for base, dirs, files in os.walk(root, followlinks=False):
        base_path = Path(base)
        for name in sorted(dirs):
            path = base_path / name
            entry_info = path.lstat()
            if stat.S_ISLNK(entry_info.st_mode):
                raise SafetyError(f"symlink is forbidden: {path}")
            if require_private and stat.S_IMODE(entry_info.st_mode) != 0o700:
                raise SafetyError(f"directory mode must be 0700: {path}")
        for name in sorted(files):
            path = base_path / name
            entry_info = path.lstat()
            if stat.S_ISLNK(entry_info.st_mode):
                raise SafetyError(f"symlink is forbidden: {path}")
            if not stat.S_ISREG(entry_info.st_mode):
                raise SafetyError(f"regular file required: {path}")
            file_mode = stat.S_IMODE(entry_info.st_mode)
            if require_private and file_mode != 0o600:
                raise SafetyError(f"artifact mode must be 0600: {path}")
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "mode": f"{file_mode:04o}",
                    "sha256": sha256_file(path),
                    "size": entry_info.st_size,
                }
            )
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"entries": entries, "sha256": sha256_bytes(encoded)}


def run_argv(
    argv: Iterable[str],
    *,
    cwd: Path | str | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    args = [str(item) for item in argv]
    if not args or any("\x00" in item for item in args):
        raise SafetyError("invalid subprocess argv")
    proc = subprocess.run(
        args,
        cwd=cwd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
    )
    if check and proc.returncode:
        message = proc.stderr.decode("utf-8", "replace").strip()[:500]
        raise SafetyError(f"command failed ({args[0]}): {message}")
    return proc
