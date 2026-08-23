# Scripted /po delivery

Use this when `/po` must be deterministic in Telegram.

## Contract

- `/po <text>` runs as a gateway `quick_commands.po` exec command.
- `/po` as a reply uses `HERMES_REPLY_TO_TEXT` as input.
- In `Hermes // Prompt Optimizer (PO) / topic 65`, plain messages that conservatively look like prompts are auto-rewritten by the gateway into `/po <text>`.
- Meta/control messages must bypass auto-routing so Chip can edit/debug the skill in the same topic.
- The script calls an LLM only to generate prompt content.
- The script validates exactly 3 prompt objects.
- The script sends exactly 3 Telegram `sendMessage` calls to the current `HERMES_CHAT_ID` and optional `HERMES_THREAD_ID`.
- The gateway quick command is `silent: true`, so successful runs do not emit a fourth final message.

## Files

- Config: `~/.hermes/config.yaml` → `quick_commands.po`
- Script: `~/.hermes/skills/po/scripts/po_quick.py`
- Gateway support: quick-command env includes `HERMES_CHAT_ID`, `HERMES_THREAD_ID`, `HERMES_REPLY_TO_TEXT`, and honors `timeout_seconds` + `silent`.

## Implementation pitfalls

- Changes in `gateway/run.py` do not take effect until `hermes-gateway` restarts.
- Prefer a delayed restart so the gateway does not kill the current response mid-flight:

```bash
sudo systemd-run --on-active=20s --unit=hermes-gateway-restart-po-$(date +%s) /bin/systemctl restart hermes-gateway
```

- Keep auto-route conservative. Do not route meta-discussion about the skill, gateway, config, quick commands, Hermes, topic/chat behavior, or control replies like “нет”, “давай”, “го”, “а можно”. The PO topic must remain usable for debugging and editing PO itself.
- For deterministic delivery, do not let the agent final response be one of the three prompts. The script must send all three messages itself, and `silent: true` must suppress a fourth gateway output.

## Smoke tests

```bash
python3 -m py_compile ~/.hermes/skills/po/scripts/po_quick.py
PO_DRY_RUN=1 HERMES_COMMAND_ARGS='Покажи свободное место' ~/.hermes/skills/po/scripts/po_quick.py
```

A live Telegram test requires running `/po <text>` after gateway restart. Also test reply-mode (`/po` as a reply) and one meta message that must bypass auto-route.
