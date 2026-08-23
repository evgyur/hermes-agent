# Parallel Deep manual create/poll fallback

Use this when a `/deep` foreground run times out or loses stdout before the final report is written, especially if the timeout may hide the `RUN_ID` line.

## Why

The canonical `deep/scripts/parallel_deep.py` creates the Parallel task and then polls until completion. If the terminal/tool timeout kills the process or truncates stdout, the remote task may be lost from the agent's view even though it was created. For high-value research, split creation and polling so the run id is saved immediately.

## Pattern

1. Write the prompt to a file under the workspace.
2. Use a small direct Parallel API wrapper that:
   - loads `PARALLEL_API_KEY` from `~/.hermes/.env`;
   - calls `POST /v1/tasks/runs`;
   - immediately writes `{run_id, interaction_id, created}` to `run.json`;
   - exits after creation.
3. Poll in a second command using `GET /v1/tasks/runs/{run_id}` and then `GET /v1/tasks/runs/{run_id}/result`.
4. Save both markdown and raw JSON artifacts.
5. Continue with targeted source checks before giving an operator verdict.

## Minimal wrapper shape

```python
# create
created = req('POST', '/tasks/runs', key, payload)
run_id = created.get('run_id') or (created.get('run') or {}).get('run_id')
Path('run.json').write_text(json.dumps({'run_id': run_id, 'created': created}, indent=2))

# poll
run_id = json.loads(Path('run.json').read_text())['run_id']
while True:
    status = req('GET', f'/tasks/runs/{run_id}', key)
    if pick_status(status) == 'completed':
        result = req('GET', f'/tasks/runs/{run_id}/result', key)
        Path('deep-report.md').write_text(extract_markdown(result))
        Path('deep-report.raw.json').write_text(json.dumps(result, indent=2))
        break
```

## Reporting rule

Do not call the research failed just because the first foreground command timed out. First check for saved report/raw files, running processes, and any saved `run.json`. If none exists and no `RUN_ID` was printed, create a new run with the split create/poll pattern rather than waiting blindly.

Do not save API keys or print them in logs. Store only `run_id`, `interaction_id`, output paths, and redacted status.
