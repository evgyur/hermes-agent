# Workflows

## Standard production workflow

```text
generate -> remove watermark -> download
```

## Execution requirement

This workflow requires an execution layer outside the skill itself.

At minimum the user needs:

- a PiAPI account;
- a PiAPI API key;
- a script, CLI, agent tool, or app that can call PiAPI endpoints.

The skill helps define and explain the workflow. It is not the transport layer.

## Why this matters

- finished video pipelines should account for watermark removal up front;
- generation price alone may understate real production cost;
- temporary output URLs should be downloaded quickly.

## Response template

When the user asks for a workflow recommendation, answer in this shape:

1. recommended model;
2. generation cost;
3. watermark-removal note;
4. final workflow.

Example:

- Recommended model: `seedance-2-preview`
- Generation cost: `$1.50` for `10s`
- Watermark removal: required, but public PiAPI USD price is undisclosed
- Workflow: `generate -> remove watermark -> download`

## Client-facing framing

Use these buckets when helpful:

- `cheap`
- `balanced`
- `premium`

Keep client-facing wording simple and avoid implementation detail unless asked.
