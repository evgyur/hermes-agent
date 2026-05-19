"""Public Hermes Power Setup quick-command helpers.

These commands are intentionally generic compatibility shims: they preserve
familiar slash-command names without shipping private profiles, account ids,
channels, sessions, or tokens.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Sequence


PUBLIC_MODEL_PROFILES = {
    "mmfast": "/model MiniMax-M2.7 --provider minimax --global",
    "gptt": "/model gpt-5.5 --provider openai-codex --global",
}


def _joined_args(argv: Sequence[str]) -> str:
    explicit = " ".join(argv).strip()
    if explicit:
        return explicit
    return os.environ.get("HERMES_COMMAND_ARGS", "").strip()


def _print_json_tool_result_media(result: str, *, media_key: str = "media_tag") -> int:
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        print(result)
        return 1
    if payload.get("success"):
        media = payload.get(media_key) or payload.get("image") or payload.get("file_path")
        if media:
            print(media)
        else:
            print(json.dumps(payload, ensure_ascii=False))
        return 0
    print(payload.get("error") or json.dumps(payload, ensure_ascii=False))
    return 1


def quick_gptprof(_: Sequence[str]) -> int:
    print("Hermes Power public model profiles")
    print("No private profiles, OAuth tokens, accounts, or operator overlays are bundled.")
    print()
    for name, target in PUBLIC_MODEL_PROFILES.items():
        print(f"/{name} -> {target}")
    print()
    print("Use `hermes power install` to add these compatibility aliases to config.yaml.")
    return 0


def quick_say(argv: Sequence[str]) -> int:
    text = _joined_args(argv)
    if not text:
        print("Usage: /say <text>")
        return 2
    from tools.tts_tool import text_to_speech_tool

    return _print_json_tool_result_media(text_to_speech_tool(text))


def quick_img(argv: Sequence[str]) -> int:
    prompt = _joined_args(argv)
    if not prompt:
        print("Usage: /img <prompt>")
        return 2
    from tools.image_generation_tool import image_generate_tool

    result = image_generate_tool(prompt=prompt)
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        print(result)
        return 1
    if payload.get("success") and payload.get("image"):
        print(payload["image"])
        return 0
    print(payload.get("error") or json.dumps(payload, ensure_ascii=False))
    return 1


def quick_video(argv: Sequence[str]) -> int:
    prompt = _joined_args(argv)
    if not prompt:
        print("Usage: /video <generation-prompt>")
        return 2
    if not os.environ.get("PIAPI_API_KEY"):
        print("PiAPI video generation is the Power Setup video surface. Set PIAPI_API_KEY locally and install/configure a PiAPI transport tool to generate videos.")
        return 1
    print("PiAPI video generation is configured by Power Setup, but this public quick-command does not bundle a PiAPI transport implementation yet. Use the bundled piapi-video-toolkit skill to choose model/cost/workflow, then call your PiAPI tool/plugin.")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("Usage: python -m hermes_cli.power_quick <gptprof|say|img|video> [args...]")
        return 2
    command, rest = argv[0], argv[1:]
    handlers = {
        "gptprof": quick_gptprof,
        "say": quick_say,
        "img": quick_img,
        "video": quick_video,
    }
    handler = handlers.get(command)
    if handler is None:
        print(f"Unknown power quick command: {command}")
        return 2
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
