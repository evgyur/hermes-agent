# Human20 MCP usage notes

Human20 Helper talks to the official Human20 MCP/API surface. It is read-only by default.

Runtime configuration is supplied by the operator environment, never by git:

```env
HUMAN20_BEARER_TOKEN=
HUMAN20_MCP_URL=https://human20.app/mcp
```

Use `scripts/entrypoint.py status` as the first smoke check. For outbound user messages, use backend-owned preview/send tools only: preview first, send only after explicit operator confirmation.

If a tool is listed but returns an authorization error, report the exact tool and route as an API capability/auth gap. Do not invent lesson, progress, transcript, or chat data.
