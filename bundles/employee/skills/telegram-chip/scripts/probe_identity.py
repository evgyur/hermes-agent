"""Privacy-safe identity probe for the employee Telegram runtime."""

import json
import os
import urllib.request


EXPECTED_BASE = "http://127.0.0.1:18083"


def get_json(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return json.load(response)


def unwrap_data(payload: dict) -> dict:
    data = payload.get("data", payload)
    return json.loads(data) if isinstance(data, str) else data


def main() -> None:
    base = os.environ.get("TELEGRAM_CHIP_BASE_URL", EXPECTED_BASE).rstrip("/")
    if base != EXPECTED_BASE:
        raise SystemExit("REFUSED_UNEXPECTED_TELEGRAM_RUNTIME")

    health = unwrap_data(get_json(base, "/health"))
    identity = unwrap_data(get_json(base, "/me"))
    if health.get("status") != "ok" or not health.get("telegram_connected"):
        raise SystemExit("CHIPMANAGER_HEALTH_FAILED")
    if identity.get("username") != "chipmanager":
        raise SystemExit("CHIPMANAGER_IDENTITY_FAILED")

    print("CHIPMANAGER_HEALTH_OK")
    print("CHIPMANAGER_IDENTITY_OK username=chipmanager")


if __name__ == "__main__":
    main()
