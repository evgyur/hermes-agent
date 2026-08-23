"""Send one employee Telegram message and verify exact readback."""

import argparse
import json
import os
import re
import urllib.parse
import urllib.request


EXPECTED_BASE = "http://127.0.0.1:18083"


def request_json(url: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def unwrap(payload: dict):
    data = payload.get("data", payload)
    return json.loads(data) if isinstance(data, str) and data.startswith(("{", "[")) else data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    base = os.environ.get("TELEGRAM_CHIP_BASE_URL", EXPECTED_BASE).rstrip("/")
    if base != EXPECTED_BASE:
        raise SystemExit("REFUSED_UNEXPECTED_TELEGRAM_RUNTIME")

    sent = request_json(base + "/messages/send", {"chat_id": args.chat_id, "message": args.message})
    if not sent.get("success"):
        raise SystemExit("CHIPMANAGER_SEND_FAILED")
    match = re.search(r"Message ID:\s*(\d+)", str(sent.get("data", "")))
    if not match:
        raise SystemExit("CHIPMANAGER_MESSAGE_ID_MISSING")
    message_id = int(match.group(1))

    chat = urllib.parse.quote(args.chat_id, safe="")
    fetched = request_json(f"{base}/chats/{chat}/messages/{message_id}")
    data = unwrap(fetched)
    if not fetched.get("success") or not isinstance(data, dict) or data.get("text") != args.message:
        raise SystemExit("CHIPMANAGER_READBACK_FAILED")

    print(f"CHIPMANAGER_SEND_OK message_id={message_id}")
    print(f"CHIPMANAGER_READBACK_OK text={args.message}")


if __name__ == "__main__":
    main()
