# Parallel Task API Deep Research Reference

Source checked: `https://docs.parallel.ai/task-api/examples/task-deep-research.md`.

## Core contract

Deep Research is for open-ended research questions, not bulk enrichment tables. It accepts a concise natural-language prompt and performs multi-step web exploration, synthesis, and citation-backed reporting.

Key constraints:

- Keep input under **15,000 characters**.
- Deep Research can take up to **45 minutes**.
- Best processors: `pro`, `ultra`; faster variants: `pro-fast`, `ultra-fast`.
- Use `text` schema for human-readable markdown reports with inline citations.
- Use `auto` schema when downstream code needs structured JSON with `basis` evidence.

## REST calls

Create run:

```bash
curl -X POST "https://api.parallel.ai/v1/tasks/runs" \
  -H "x-api-key: $PARALLEL_API_KEY" \
  -H "Content-Type: application/json" \
  --data-raw '{
    "input": "Research question...",
    "processor": "ultra",
    "task_spec": {
      "output_schema": {
        "type": "text",
        "description": "Return a rigorous markdown report with citations."
      }
    }
  }'
```

Poll/result:

```bash
curl -s "https://api.parallel.ai/v1/tasks/runs/${RUN_ID}/result" \
  -H "x-api-key: $PARALLEL_API_KEY"
```

The create response returns `run_id` and often `interaction_id`. The result response includes run status while pending and final output after completion. SDK examples also use `retrieve()` for status and `result()` for final output.

## Follow-up research

For follow-up questions, pass `previous_interaction_id` and usually use a cheaper/faster processor (`core`, `pro-fast`) because Parallel carries context from the original research interaction.

## Operational defaults for Hermes

- Use the bundled helper: `~/.hermes/skills/deep/scripts/parallel_deep.py`.
- Default processor for serious research: `ultra`.
- Default schema: `text`.
- Poll every 20 seconds, timeout 3600 seconds.
- Save both markdown and raw JSON under `~/.hermes/research/` or task-specific `/tmp/...` path.
- Never print API keys. Keep `PARALLEL_API_KEY` in `~/.hermes/.env` or process env.
