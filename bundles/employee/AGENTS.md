# Employee runtime rules

For any Telegram task involving `telegram-chip` or `chipmanager`, the employee
capability is already provisioned. Start with this terminal command:

```bash
python3 ~/.hermes/skills/telegram-chip/scripts/probe_identity.py
```

Do not inspect or reload the skill before or after this command. The command is
the complete required entrypoint. Run it once, then use its result.

For an authorized write with an exact target and text, run:

```bash
python3 ~/.hermes/skills/telegram-chip/scripts/send_and_read.py --chat-id TARGET --message TEXT
```

Pass `TARGET` and `TEXT` as separate shell arguments. This helper performs the
mandatory readback; do not rebuild its HTTP calls by hand.

Proceed only after both OK markers. Use the account and endpoint defined by the
employee skill. Never connect to any personal Telegram runtime or use Computer
Use, Telegram Desktop, or browser automation for Telegram.
