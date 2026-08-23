# Hermes hooks deep-research caveats

Use this when `/deep` research is about Hermes Agent hooks, middleware, gateway lifecycle, or operator guardrails.

## Durable findings from a Hermes hooks `/deep` run

- Public examples of Hermes-specific hooks are still thin. Treat official Hermes docs and the live local Hermes install as primary sources; community/blog results are supporting evidence only.
- The useful operator pattern is not “install many clever hooks.” It is a small guardrail set:
  - `pre_tool_call` for dangerous command / prod approval blocking;
  - `post_tool_call` for audit/evidence ledger;
  - `pre_llm_call` for compact turn-scoped context injection;
  - `pre_gateway_dispatch` for Telegram/group policy;
  - `gateway:startup` for explicit startup checks;
  - `subagent_stop` for orchestration logging.
- Hermes hook and middleware systems are usually fail-open on script/plugin errors. For any blocking hook, recommend a paired canary/audit/alert path so a broken guardrail is noticed.
- Shell-hook consent is keyed by `(event, command)` and stored in `~/.hermes/shell-hooks-allowlist.json`; script edits do not automatically invalidate old consent. `hermes hooks doctor` is the verification step after changing hook scripts/config.
- Do not trust `/deep` output for exact config shape without checking current official docs/local CLI. A prior deep report mixed an outdated/incorrect list-style shell-hook config with the current nested event-map shape.

## Correct current shell-hook config shape to verify against

Prefer the official nested event-map form:

```yaml
hooks:
  pre_tool_call:
    - matcher: "terminal"
      command: "~/.hermes/agent-hooks/block-dangerous.sh"
      timeout: 5
hooks_auto_accept: false
```

Before advising implementation, verify with the live install where possible:

```bash
hermes hooks list
hermes hooks doctor
hermes hooks test pre_tool_call --for-tool terminal
```

## Synthesis rule

For Hermes-internal topics, final `/deep` synthesis must explicitly separate:

1. official Hermes docs / local CLI evidence;
2. source-code evidence from `NousResearch/hermes-agent`;
3. third-party examples such as Akto Guardrails or NVIDIA NeMo Relay;
4. weak blogs / mirrored docs / generic Claude Code hook content.

If these conflict, prefer official docs + local runtime and call out the mismatch.
