# Telegram Chip

A single-client Telegram integration that exposes:

- **HTTP API** (FastAPI, port `8080`)
- **MCP stdio server** (optional, for OpenClaw integration)
- **Sales webhook hooks** (optional, real-time message triggers)
- **tgdl export** (built-in export to JSON)

## Why telegram-chip

telegram-chip is designed to avoid session conflicts by keeping **one Telethon client** as the source of truth. All access (HTTP API, event hooks, tgdl) goes through the same client instance.

## Architecture

```
Telegram
   │
   ▼
Telethon (single client)
   │
   ├── HTTP API (FastAPI)
   ├── MCP stdio (optional)
   └── Sales Webhook (optional)
```

## Services

- **telegram-chip-api.service** → HTTP API on `:8080`
- **telegram-chip-mcp.service** → MCP stdio (optional, disabled by default)

Check status:
```bash
systemctl status telegram-chip-api
```

## Configuration (.env)

Located at: `/opt/telegram-chip/.env`

Required:
```
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION_STRING=...
```

Optional (Sales webhook):
```
SALES_WEBHOOK_URL=http://localhost:18789/api/cron/wake
SALES_MONITORED_USERS=12345,67890   # empty = all private chats
```

## HTTP API

Health:
```
GET /health
```

Messages:
```
GET  /chats/{chat_id}/messages?limit=50
POST /messages/send
POST /messages/edit
POST /messages/delete
```

Example:
```bash
curl -X POST http://localhost:8080/messages/send \
  -H "Content-Type: application/json" \
  -d chat_id:123456789
```

## tgdl export

Exports chat history to JSON using the same Telethon client.

```
POST /tgdl/export?chat_id=<id>&limit=500
```

Response:
```json
{
  "success": true,
  "data": {
    "status": "ok",
    "count": 500,
    "path": "/opt/telegram-chip/exports/<chat_id>.json"
  }
}
```

## MCP stdio (optional)

The MCP server lives in `main.py` and is disabled by default. Enable it only if you need OpenClaw MCP tools via stdio:

```bash
systemctl enable telegram-chip-mcp
systemctl start telegram-chip-mcp
```


## MCP stdio (when it is needed)

MCP stdio is only required if you want Telegram to act as an MCP tool.
Use it when:
- OpenClaw (or another MCP client) needs **direct tool calls** via MCP protocol
- You want Telegram as a **native tool** inside an LLM agent chain
- You need **strict tool isolation** without HTTP

If you already use the HTTP API, MCP stdio is **not required** and can be ignored.


## Notes

- Keep **one active session** to avoid Telegram AuthKey conflicts.
- If the session is reused from another IP, generate a new session string.
- For sales automation, set `SALES_WEBHOOK_URL` and optionally `SALES_MONITORED_USERS`.

EOF cat > /opt/telegram-chip/README.md << "EOF"
# telegram-chip

A single-client Telegram integration that exposes:

- **HTTP API** (FastAPI, port `8080`)
- **MCP stdio server** (optional, for OpenClaw integration)
- **Sales webhook hooks** (optional, real-time message triggers)
- **tgdl export** (built-in export to JSON)

## Why telegram-chip

telegram-chip is designed to avoid session conflicts by keeping **one Telethon client** as the source of truth. All access (HTTP API, event hooks, tgdl) goes through the same client instance.

## Architecture

```
Telegram
   │
   ▼
Telethon (single client)
   │
   ├── HTTP API (FastAPI)
   ├── MCP stdio (optional)
   └── Sales Webhook (optional)
```

## Services

- **telegram-chip-api.service** → HTTP API on `:8080`
- **telegram-chip-mcp.service** → MCP stdio (optional, disabled by default)

Check status:
```bash
systemctl status telegram-chip-api
```

## Configuration (.env)

Located at: `/opt/telegram-chip/.env`

Required:
```
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION_STRING=...
```

Optional (Sales webhook):
```
SALES_WEBHOOK_URL=http://localhost:18789/api/cron/wake
SALES_MONITORED_USERS=12345,67890   # empty = all private chats
```

## HTTP API

Health:
```
GET /health
```

Messages:
```
GET  /chats/{chat_id}/messages?limit=50
POST /messages/send
POST /messages/edit
POST /messages/delete
```

Example:
```bash
curl -X POST http://localhost:8080/messages/send \
  -H "Content-Type: application/json" \
  -d message:Hello
```

## tgdl export

Exports chat history to JSON using the same Telethon client.

```
POST /tgdl/export?chat_id=<id>&limit=500
```

Response:
```json
{
  "success": true,
  "data": {
    "status": "ok",
    "count": 500,
    "path": "/opt/telegram-chip/exports/<chat_id>.json"
  }
}
```

## MCP stdio (optional)

The MCP server lives in `main.py` and is disabled by default. Enable it only if you need OpenClaw MCP tools via stdio:

```bash
systemctl enable telegram-chip-mcp
systemctl start telegram-chip-mcp
```


## MCP stdio (when it is needed)

MCP stdio is only required if you want Telegram to act as an MCP tool.
Use it when:
- OpenClaw (or another MCP client) needs **direct tool calls** via MCP protocol
- You want Telegram as a **native tool** inside an LLM agent chain
- You need **strict tool isolation** without HTTP

If you already use the HTTP API, MCP stdio is **not required** and can be ignored.


## Notes

- Keep **one active session** to avoid Telegram AuthKey conflicts.
- If the session is reused from another IP, generate a new session string.
- For sales automation, set `SALES_WEBHOOK_URL` and optionally `SALES_MONITORED_USERS`.
