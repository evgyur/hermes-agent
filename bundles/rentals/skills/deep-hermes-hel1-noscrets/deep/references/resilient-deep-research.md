# Resilient /deep research pattern

Use when a deep-research run is slow, times out locally, or the gateway/session is interrupted.

## Pattern

1. Start with the canonical runner and save artifacts:
   ```bash
   ~/.hermes/skills/deep/scripts/parallel_deep.py "<prompt>" \
     --processor ultra \
     --schema text \
     --timeout 3600 \
     --output /tmp/<topic>_deep.md
   ```
2. If the foreground command times out locally, do **not** assume the Parallel task failed. Capture `RUN_ID` / `INTERACTION_ID` from stdout if available.
3. Relaunch in background with `notify_on_complete=true` or poll the existing run via the Parallel API. The runner prints run IDs before polling, so the remote task can outlive the local wait/gateway process.
4. If the run remains slow and the user needs an answer now, start a narrower `pro-fast` / `ultra-fast` follow-up with a tighter prompt, while continuing to treat the original run as potentially useful later.
5. Verify weak or surprising deep-research claims with targeted extraction/search before finalizing, especially claims about unofficial community programs, X posts, grants, or foundation status.

## Quality rule

When a user explicitly says “not X” (for example, “not builder codes”), make that exclusion a first-class line in the research prompt and in the synthesis. Do not let the report collapse back into the adjacent better-documented topic.

## Final response contract after a disrupted run

Report:
- which run completed vs remained running;
- artifact path if saved;
- the short answer with confidence/caveats;
- source URLs for material claims.

Do not say a deep run failed just because a local terminal wait/gateway was interrupted.
