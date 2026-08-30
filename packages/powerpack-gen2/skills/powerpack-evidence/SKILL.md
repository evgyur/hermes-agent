---
name: powerpack-evidence
description: Reconcile capability claims with canonical runtime receipts.
---

# Powerpack evidence reconciliation

Use this skill when a user or operator claim conflicts with an earlier install,
tool, deployment, or health result.

## Procedure

1. Identify the component's canonical receipt and declared runtime manifest.
2. Probe the declared interpreter and executable directly.
3. Check the recorded version, owner, cache/model roots, and removal contract.
4. Compare timestamps and exact identifiers before accepting either claim.
5. Report the evidence and correction. Do not infer absence from the main
   Hermes venv or ambient `PATH` when the component owns a managed runtime.

Never expose secret values while checking receipts or runtime configuration.
