---
name: shaw
description: "A lightweight engineering quality governor for coding, debugging, refactors, integrations, deployments, and repo maintenance. Use it when false completion is costly: inspect reality, make the smallest safe change, verify with fresh evidence, and report honestly."
version: 1.0.0-public
license: MIT
metadata:
  tags: [coding, debugging, refactor, verification, workflow, engineering]
---

# Shaw

Shaw is a compact engineering governor for AI coding agents and human operators.

It is not a framework, private process, or internal runbook. It is a practical discipline:

1. inspect before changing;
2. state assumptions that affect scope;
3. choose the smallest viable path;
4. keep changes surgical;
5. verify with fresh evidence;
6. report what changed, what passed, and what remains risky.

Use Shaw when a confident-looking but unverified answer would be worse than a slower, proven one.

## When to use

Use Shaw for:

- coding tasks;
- debugging production or local failures;
- refactors;
- dependency or integration work;
- deployment preparation;
- configuration changes;
- tests and CI fixes;
- repo maintenance;
- code review follow-up;
- any multi-step task where false “done” is costly.

Do not run a heavy process for:

- one-line factual answers;
- pure writing tasks;
- obvious typo fixes with trivial verification;
- brainstorming where no implementation claim is made.

Even for small tasks, keep the core rule: do not claim completion without evidence.

## Core loop

```text
Plan → Implement → Verify → Report
```

### 1. Plan

Before editing, establish:

```text
goal: what must become true
assumptions: what matters if wrong
known facts: what was verified from files, logs, tests, docs, or runtime
unknowns: what is not yet verified
smallest path: the least risky change that can satisfy the goal
verification: the exact test, command, smoke check, or review needed
```

If two interpretations lead to different changes, ask or explicitly choose the safer one.

### 2. Implement

Rules:

- inspect relevant files before editing;
- prefer narrow patches over rewrites;
- avoid unrelated cleanup;
- keep sensitive runtime material out of code, logs, screenshots, and final reports;
- do not change production, billing, DNS, auth, private runtime material, or public-facing behavior unless the user explicitly asked for it;
- preserve rollback paths for risky changes.

### 3. Verify

Verification should match risk.

Examples:

- syntax check for small code edits;
- focused unit test for a bugfix;
- integration smoke for routing/API changes;
- CLI command output for operational fixes;
- screenshot or browser check for UI work;
- diff review for refactors.

Never say “works” only because the code looks right.

### 4. Report

A good final report is short and evidence-first:

```text
result: what is now true
changed: files or systems changed
verified: exact commands/checks and outcomes
not touched: relevant boundaries left unchanged
risk: remaining gaps or follow-up
```

If blocked, say what blocks the work and what evidence led there.

## Debugging mode

Use this for bugs, failing tests, broken deployments, or unexpected behavior.

1. Reproduce or capture the failing signal.
2. Read the full error, not just the summary.
3. Check recent changes.
4. Trace the data flow to the failing boundary.
5. Form one root-cause hypothesis.
6. Test the hypothesis with the smallest possible change or probe.
7. Fix the root cause, not the symptom.
8. Add or run a regression check when feasible.

Red flags:

- “Let’s just try this.”
- “Probably X.”
- “It should work now.”
- Multiple unrelated fixes at once.
- No fresh test after the fix.

If three fixes fail, stop and reassess the architecture instead of stacking another patch.

## Review mode

Use this before claiming a meaningful code change is ready.

Check:

- Does the change solve the actual request?
- Is the diff smaller than the problem requires?
- Are auth, sensitive data, data loss, migrations, concurrency, and rollback considered?
- Are tests or smokes enough for the risk?
- Did the change introduce unrelated behavior?

Separate blocking issues from minor notes.

## Production-adjacent mode

For anything that can affect real users, money, credentials, routing, or external systems:

1. inspect current state;
2. back up config/data when relevant;
3. make one change at a time;
4. verify with live but bounded checks;
5. keep rollback clear;
6. report exact evidence.

Do not hide partial failure. A safe blocker is better than fake progress.

## Output templates

### Short task

```text
Done.

Changed:
- <file or behavior>

Verified:
- <command/check> → <result>

Remaining:
- <none or risk>
```

### Debugging report

```text
Found the cause: <root cause>.

Evidence:
- <log/error/test>

Fix:
- <small change>

Verified:
- <command/check> → <result>

Risk:
- <remaining caveat>
```

### Blocked report

```text
Blocked by <specific blocker>.

Verified:
- <what was checked>

Needed next:
- <credential/access/decision/data>
```

## Done criteria

A task is done only when:

- the requested outcome is satisfied;
- relevant checks passed or limitations are stated;
- the final report includes real evidence;
- no unrelated changes are bundled in;
- private data was not exposed.
