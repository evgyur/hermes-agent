"""Regression tests for gateway auto-appended media delivery.

A stale ``image_generate`` tool result is stored as JSON, not as a literal
``MEDIA:`` tag.  If historical JSON paths are not counted as already-delivered
media, later text-only turns can re-send old generated images.
"""

from gateway.run import (
    _collect_auto_append_media_tags,
    _extract_deliverable_media_paths_from_tool_content,
)


def _assistant_call(call_id: str, name: str):
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_id,
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


def _tool_result(call_id: str, content: str):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_extract_json_image_generate_path_for_history_dedup():
    content = '{"success": true, "image": "/tmp/generated-bike-bg.png"}'

    assert _extract_deliverable_media_paths_from_tool_content(content) == [
        "/tmp/generated-bike-bg.png"
    ]


def test_history_json_image_path_blocks_stale_auto_append():
    old_path = "/tmp/old-generated-background.png"
    messages = [
        _assistant_call("call_old", "image_generate"),
        _tool_result("call_old", f'{{"success": true, "image": "{old_path}"}}'),
        {"role": "user", "content": "plain text follow-up"},
        {"role": "assistant", "content": "ok, no media this turn"},
    ]

    media_tags, has_voice = _collect_auto_append_media_tags(
        messages,
        history_offset=0,  # compression/cached fallback scans the whole list
        history_media_paths={old_path},
    )

    assert media_tags == []
    assert has_voice is False


def test_current_turn_image_generate_json_still_auto_appends():
    new_path = "/tmp/new-generated-background.png"
    messages = [
        {"role": "user", "content": "make image"},
        _assistant_call("call_new", "image_generate"),
        _tool_result("call_new", f'{{"success": true, "image": "{new_path}"}}'),
        {"role": "assistant", "content": "ready"},
    ]

    media_tags, has_voice = _collect_auto_append_media_tags(
        messages,
        history_offset=1,
        history_media_paths=set(),
    )

    assert media_tags == [f"MEDIA:{new_path}"]
    assert has_voice is False
