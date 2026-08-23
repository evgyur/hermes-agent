#!/usr/bin/env python3
"""Deterministic /po quick command: generate 3 prompts, send as 3 Telegram messages."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
ENV_PATH = HERMES_HOME / ".env"
DEFAULT_MODEL = os.environ.get("PO_MINIMAX_MODEL", "MiniMax-M2.7")
DEFAULT_BASE_URL = os.environ.get("PO_MINIMAX_BASE_URL", "https://api.minimax.io/anthropic")
DEFAULT_TIMEOUT = int(os.environ.get("PO_TIMEOUT_SECONDS", "90"))

SYSTEM_PROMPT = """Ты — Prompt Optimizer. Твоя единственная задача: превратить входной запрос пользователя в ровно 3 сильных промпта.

Правила:
1. Не выполняй саму задачу пользователя.
2. Используй только данные из запроса пользователя, не выдумывай факты.
3. Поддерживай язык пользователя.
4. Тон: профессиональный с элементами коучинга.
5. Prompt 1: экспертная роль.
6. Prompt 2: формулировка от первого лица.
7. Prompt 3: детализированный структурный промпт с контекстом, ограничениями и требованиями к выходу.
8. Без markdown и HTML внутри body.
9. Верни строго JSON-массив из 3 объектов. Никакого текста вокруг JSON.

Схема:
[
  {"title":"Prompt 1: Экспертная роль", "body":"..."},
  {"title":"Prompt 2: Формулировка от первого лица", "body":"..."},
  {"title":"Prompt 3: Детализированный структурный промпт", "body":"..."}
]
"""


def load_dotenv(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def command_text(argv: list[str]) -> str:
    args = " ".join(argv[1:]).strip()
    env_args = os.environ.get("HERMES_COMMAND_ARGS", "").strip()
    reply_text = os.environ.get("HERMES_REPLY_TO_TEXT", "").strip()
    # Gateway append_args can pass a shell-quoted single argv; env args is canonical.
    text = env_args or args
    if not text:
        for key in ("HERMES_RAW_PLATFORM_TEXT", "HERMES_RAW_TEXT"):
            raw = os.environ.get(key, "").strip()
            if not raw.startswith("/"):
                continue
            parts = raw.split(maxsplit=1)
            if len(parts) < 2:
                continue
            cmd = parts[0][1:].lower().split("@", 1)[0]
            if cmd == "po":
                text = parts[1].strip()
                break
    if not text and reply_text:
        if reply_text.strip().lower().startswith("/po "):
            text = reply_text.strip().split(maxsplit=1)[1].strip()
        else:
            text = reply_text
    return text.strip()


def minimax_messages(prompt: str) -> str:
    load_dotenv()
    api_key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY is not set")

    base = DEFAULT_BASE_URL.rstrip("/")
    url = f"{base}/v1/messages"
    payload = {
        "model": DEFAULT_MODEL,
        "max_tokens": 2200,
        "temperature": 0.25,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"MiniMax HTTP {exc.code}: {body}") from exc

    content = data.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                txt = item.get("text") or item.get("content")
                if isinstance(txt, str):
                    parts.append(txt)
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(parts).strip()
    else:
        text = str(data.get("text") or data.get("message") or "").strip()
    if not text:
        raise RuntimeError(f"MiniMax returned empty content: {json.dumps(data, ensure_ascii=False)[:800]}")
    return text


def extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[\s*\{.*\}\s*\]", text, re.S)
        if match:
            return json.loads(match.group(0))
        raise


def parse_prompts(text: str) -> list[dict[str, str]]:
    try:
        data = extract_json(text)
        if isinstance(data, dict) and isinstance(data.get("prompts"), list):
            data = data["prompts"]
        if not isinstance(data, list):
            raise ValueError("JSON root is not a list")
        prompts: list[dict[str, str]] = []
        for i, item in enumerate(data, 1):
            if not isinstance(item, dict):
                raise ValueError(f"item {i} is not an object")
            title = str(item.get("title") or f"Prompt {i}").strip()
            body = str(item.get("body") or item.get("text") or "").strip()
            if not body:
                raise ValueError(f"item {i} body is empty")
            prompts.append({"title": title, "body": body})
        if len(prompts) != 3:
            raise ValueError(f"expected 3 prompts, got {len(prompts)}")
        return prompts
    except Exception:
        # Last-chance parser for accidental plaintext output.
        chunks = re.split(r"(?=Prompt\s*[123]\s*:)", text.strip(), flags=re.I)
        chunks = [c.strip() for c in chunks if c.strip()]
        prompts = []
        for i, chunk in enumerate(chunks[:3], 1):
            first, _, rest = chunk.partition("\n")
            title = first.strip() or f"Prompt {i}"
            body = rest.strip() or chunk.strip()
            prompts.append({"title": title, "body": body})
        if len(prompts) == 3:
            return prompts
        raise


def render_prompt(item: dict[str, str]) -> str:
    return f"{item['title']}\n{item['body']}".strip()


def telegram_send(text: str) -> None:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("HERMES_CHAT_ID", "").strip()
    thread_id = os.environ.get("HERMES_THREAD_ID", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not chat_id:
        raise RuntimeError("HERMES_CHAT_ID is not set")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    data = urllib.parse.urlencode(payload).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"Telegram HTTP {exc.code}: {body}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram send failed: {json.dumps(result, ensure_ascii=False)[:800]}")


def main(argv: list[str]) -> int:
    dry_run = os.environ.get("PO_DRY_RUN", "").lower() in {"1", "true", "yes"} or "--dry-run" in argv
    argv = [x for x in argv if x != "--dry-run"]
    text = command_text(argv)
    if not text:
        print("Пришли текст после /po или ответь /po на сообщение с текстом.")
        return 2

    raw = minimax_messages(text)
    prompts = parse_prompts(raw)
    if len(prompts) != 3:
        raise RuntimeError(f"expected exactly 3 prompts, got {len(prompts)}")

    if dry_run:
        print(json.dumps(prompts, ensure_ascii=False, indent=2))
        return 0

    for item in prompts:
        telegram_send(render_prompt(item))
        time.sleep(0.25)
    # For quick_commands with silent:true the gateway will not send this stdout.
    print("ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Exception as exc:
        print(f"/po error: {exc}", file=sys.stderr)
        raise SystemExit(1)
