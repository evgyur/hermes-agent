# No-agent watchdogs

Use this pattern for deterministic scheduled scripts whose output is delivered directly to an operator.

## Contract

- Exit `0` with empty stdout: green, no message.
- Exit `0` with non-empty stdout: send this text as an alert or report.
- Exit non-zero: monitor runner failed, or a fail-visible guardrail intentionally fired.
- Stderr is diagnostic only; it should not be delivered as a normal user alert unless the scheduler explicitly surfaces failures.

## Pattern

```python
import sys

def warn_stderr(message: str) -> None:
    print(message, file=sys.stderr)

try:
    result = probe()
except TimeoutError as exc:
    warn_stderr(f"probe timeout: {exc}")
    raise SystemExit(0)  # known transient dependency failure, silent on green

if result.actionable:
    print(result.message)

raise SystemExit(0)
```

## Rules

- Use bounded network timeouts.
- Avoid all-day polling of expensive APIs when no live event window exists.
- Deduplicate repeated alerts and emit a recovery message when useful.
- If the script sends a message itself, keep stdout empty to avoid duplicate delivery.
