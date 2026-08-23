# Public-clean video generation skill

This archive contains a sanitized, reusable agent skill for AI video-generation workflows.

## What is included

- `SKILL.md` — main skill instructions.
- `references/review-checklist.md` — QA checklist before delivery.
- `references/provider-evaluation.md` — how to classify new video providers.
- `references/provider-cli-template.md` — generic CLI/API wrapper shape with placeholders only.
- `AGENT_HANDOFF.md` — how another agent should install and use this bundle.

## What was intentionally removed

- private user/project names;
- local machine paths;
- chat/channel IDs;
- credentials and environment-specific config;
- internal runtime locations;
- private operational runbooks.

## Install

Copy this folder into your agent's skills directory, then load the skill by name:

```text
video-generation-router
```

Wire it to your own provider CLI or API wrapper before using it for real generation.
