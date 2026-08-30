"""Maintained Telegram plugin authority and private-context safety gates."""

import json
import os
from pathlib import Path
import stat
import urllib.parse

from unittest.mock import MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

PROFILE = "hermesdev"
CHAT_ID = "-1003971448755"
THREAD_ID = "24901"
OWNER_ID = "12345"
PRIVATE_LINK = "https://t.me/c/3971448755/26452/47266"


def _adapter():
    cls = load_plugin_adapter("telegram").TelegramAdapter
    adapter = object.__new__(cls)
    adapter.platform = Platform.TELEGRAM
    adapter.config = PlatformConfig(
        enabled=True,
        token="fake",
        extra={
            "telegram_chip_context_routes": [{
                "profile": PROFILE,
                "chat_id": CHAT_ID,
                "thread_id": THREAD_ID,
                "user_id": OWNER_ID,
            }]
        },
    )
    return adapter


def _event(text, *, reply_to_message_id=None, reply_to_text=None):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            profile=PROFILE,
            chat_id=CHAT_ID,
            thread_id=THREAD_ID,
            chat_type="group",
            user_id=OWNER_ID,
        ),
        message_id="1",
        reply_to_message_id=reply_to_message_id,
        reply_to_text=reply_to_text,
    )


@pytest.mark.asyncio
async def test_topic_root_is_routing_only_and_all_private_links_are_resolved():
    adapter = _adapter()
    root = _event("Го", reply_to_message_id=THREAD_ID, reply_to_text=None)
    adapter._telegram_chip_fetch_message_sync = MagicMock(return_value={"text": "root"})
    assert await adapter._resolve_telegram_chip_context(root) is False
    adapter._telegram_chip_fetch_message_sync.assert_not_called()

    second = "https://t.me/c/3971448755/26452/47267"
    event = _event(f"Compare {PRIVATE_LINK} and {second}")
    adapter._telegram_chip_fetch_message_sync = MagicMock(
        side_effect=lambda _chat, message: {"text": f"context-{message}"}
    )
    assert await adapter._resolve_telegram_chip_context(event) is True
    assert adapter._telegram_chip_fetch_message_sync.call_count == 2
    assert len(event.metadata["telegram_chip_resolution"]["sources"]) == 2


def test_telegram_chip_rejects_non_loopback_origin_before_network(monkeypatch):
    adapter = _adapter()
    adapter.config.extra["telegram_chip_base_url"] = "http://example.test:8080"
    urlopen = MagicMock()
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(ValueError):
        adapter._telegram_chip_fetch_message_sync(CHAT_ID, 1)
    urlopen.assert_not_called()


def test_telegram_chip_decodes_the_deployed_outer_message_envelope(monkeypatch):
    adapter = _adapter()
    inner = {
        "id": 47266,
        "text": "production-shaped context",
        "has_media": True,
    }
    body = json.dumps(
        {"success": True, "data": json.dumps(inner), "error": None}
    ).encode()

    class _Headers(dict):
        pass

    class _Response:
        headers = _Headers({"Content-Length": str(len(body))})

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def geturl(self):
            return f"http://127.0.0.1:8080/chats/{CHAT_ID}/messages/47266"

        def read(self, _limit):
            return body

    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _Response())

    assert adapter._telegram_chip_fetch_message_sync(CHAT_ID, 47266) == inner


def test_telegram_chip_decodes_the_deployed_media_path_envelope(
    monkeypatch, tmp_path
):
    adapter = _adapter()
    requested_paths = []
    acl_calls = []

    def grant_chip_acl(command, **kwargs):
        acl_calls.append((command, kwargs))
        os.chmod(command[-1], 0o770)
        return MagicMock(returncode=0)

    if os.name != "nt":
        monkeypatch.setattr("subprocess.run", grant_chip_acl)

    class _Headers(dict):
        def get_content_type(self):
            return "application/json"

    class _Response:
        def __init__(self, body):
            self._body = body
            self.headers = _Headers({"Content-Length": str(len(body))})

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _limit):
            return self._body

    class _Opener:
        def open(self, request, **_kwargs):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            media_path = query["output_path"][0]
            requested_paths.append(media_path)
            assert Path(media_path).suffix == ".ogg"
            assert not Path(media_path).exists()
            if os.name != "nt":
                assert stat.S_IMODE(Path(media_path).parent.stat().st_mode) == 0o770
            Path(media_path).write_bytes(b"production-shaped audio")
            body = json.dumps(
                {
                    "success": True,
                    "data": json.dumps(
                        {"success": True, "path": media_path, "error": None}
                    ),
                    "error": None,
                }
            ).encode()
            return _Response(body)

    monkeypatch.setattr("urllib.request.build_opener", lambda *_a: _Opener())

    returned = adapter._telegram_chip_media_download_sync(CHAT_ID, 47266)

    assert returned == requested_paths[0]
    assert Path(returned).is_file()
    if os.name != "nt":
        assert acl_calls and acl_calls[0][0][:3] == [
            "/usr/bin/setfacl",
            "-m",
            "u:chip:rwx",
        ]
        assert stat.S_IMODE(Path(returned).parent.stat().st_mode) == 0o700
    Path(returned).unlink()
    Path(returned).parent.rmdir()


def test_telegram_chip_cleanup_never_removes_a_generic_temp_parent(tmp_path):
    adapter = _adapter()
    generic_path = tmp_path / "fallback.mp3"
    generic_path.write_bytes(b"audio")

    adapter._cleanup_telegram_chip_media_path(str(generic_path))

    assert not generic_path.exists()
    assert tmp_path.is_dir()


def test_telegram_chip_cleanup_removes_its_owned_temp_parent(tmp_path):
    adapter = _adapter()
    owned_parent = tmp_path / "hermes-telegram-chip-media-test"
    owned_parent.mkdir()
    owned_path = owned_parent / "media"
    owned_path.write_bytes(b"audio")

    adapter._cleanup_telegram_chip_media_path(str(owned_path))

    assert not owned_parent.exists()


def test_telegram_chip_never_accepts_or_deletes_an_unowned_media_path(
    monkeypatch, tmp_path
):
    adapter = _adapter()
    unowned = tmp_path / "keep.ogg"
    unowned.write_bytes(b"must survive")
    requested_paths = []

    class _Headers(dict):
        def get_content_type(self):
            return "application/json"

    class _Response:
        def __init__(self, body):
            self._body = body
            self.headers = _Headers({"Content-Length": str(len(body))})

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _limit):
            return self._body

    class _Opener:
        def open(self, request, **_kwargs):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            requested_paths.append(query["output_path"][0])
            body = json.dumps(
                {
                    "success": True,
                    "data": json.dumps(
                        {"success": True, "path": str(unowned), "error": None}
                    ),
                    "error": None,
                }
            ).encode()
            return _Response(body)

    monkeypatch.setattr("urllib.request.build_opener", lambda *_a: _Opener())

    with pytest.raises(ValueError, match="unowned media path"):
        adapter._telegram_chip_media_download_sync(CHAT_ID, 47266)

    assert unowned.read_bytes() == b"must survive"
    assert requested_paths and not Path(requested_paths[0]).exists()


@pytest.mark.parametrize("case", ["error", "oversize", "symlink"])
def test_telegram_chip_cleans_only_its_owned_target_on_media_failure(
    monkeypatch, tmp_path, case
):
    adapter = _adapter()
    requested_paths = []
    symlink_target = tmp_path / "symlink-target.ogg"
    symlink_target.write_bytes(b"must survive")

    class _Headers(dict):
        def get_content_type(self):
            return "application/json"

    class _Response:
        def __init__(self, body):
            self._body = body
            self.headers = _Headers({"Content-Length": str(len(body))})

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _limit):
            return self._body

    class _Opener:
        def open(self, request, **_kwargs):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            owned_path = query["output_path"][0]
            requested_paths.append(owned_path)
            if case == "error":
                payload = {"success": False, "data": None, "error": "download failed"}
            else:
                if case == "oversize":
                    with open(owned_path, "wb") as oversized:
                        oversized.truncate(64 * 1024 * 1024 + 1)
                else:
                    if Path(owned_path).exists():
                        Path(owned_path).unlink()
                    try:
                        os.symlink(symlink_target, owned_path)
                    except OSError:
                        pytest.skip("symlinks are unavailable on this platform")
                payload = {
                    "success": True,
                    "data": json.dumps(
                        {"success": True, "path": owned_path, "error": None}
                    ),
                    "error": None,
                }
            return _Response(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.build_opener", lambda *_a: _Opener())

    with pytest.raises((RuntimeError, ValueError)):
        adapter._telegram_chip_media_download_sync(CHAT_ID, 47266)

    assert requested_paths and not Path(requested_paths[0]).exists()
    assert not Path(requested_paths[0]).parent.exists()
    assert symlink_target.read_bytes() == b"must survive"


@pytest.mark.asyncio
async def test_transcribe_multilink_recovery_is_atomic_and_ordered(tmp_path):
    adapter = _adapter()
    adapter.config.extra["transcribe_routes"] = [
        {
            "enabled": True,
            "profile": PROFILE,
            "chat_id": CHAT_ID,
            "thread_id": THREAD_ID,
        }
    ]
    second = "https://t.me/c/3971448755/26452/47267"
    event = _event(f"Transcribe {PRIVATE_LINK} and {second}")
    event.message_type = MessageType.TEXT
    adapter._telegram_chip_fetch_message_sync = MagicMock(
        return_value={"has_media": True}
    )
    paths = []

    def _download(_chat_id, message_id):
        if message_id == 47267:
            raise RuntimeError("second download failed")
        path = tmp_path / f"{message_id}.mp3"
        path.write_bytes(b"audio")
        paths.append(path)
        return str(path)

    adapter._telegram_chip_media_download_sync = _download
    with pytest.raises(RuntimeError, match="second download"):
        await adapter._recover_transcribe_route_tme_link_via_telegram_chip(
            event, CHAT_ID
        )

    assert adapter._telegram_chip_fetch_message_sync.call_count == 2
    assert event.media_urls == []
    assert event.message_type is MessageType.TEXT
    assert all(not path.exists() for path in paths)
