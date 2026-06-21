# Context anchor and wrong-goal recovery for `Make supergoal`

Use when the user/operator says `Make supergoal`, `делай`, or replies to an earlier plan/status while several nearby topics exist.

## Problem captured

A stale standing-goal wrapper and a separate operational bug can dominate the visible transcript. In one session, `Make supergoal` was incorrectly interpreted as “make a SuperGoal for GoalManager spam,” while Chip’s intended thread was “make the Polymarket/chip-privy skill/runtime goal.” This produced a wrong SuperGoal artifact and user correction: `Не на это` / `Мы хотели сделать скил Polymarket` / `А потом ты все сломал`.

## Rule

Before creating a SuperGoal, anchor the request to the **semantic object of the replied-to/latest human correction**, not to the noisiest recent system failure.

## Required sequence

1. Inspect the latest human message and reply target:
   - If Chip replies to `Make supergoal`, treat it as “create the SuperGoal for the plan/topic immediately above that message,” not as a fresh brainstorm.
   - If Chip then corrects with `не на это`, stop the wrong thread immediately.
2. Resolve the target object explicitly before writing files:
   - target skill/repo/project;
   - intended outcome;
   - whether this is a skill scaffold, runtime implementation, or live activation.
3. If a wrong SuperGoal was already created:
   - quarantine/move the wrong artifact out of the working namespace;
   - verify the intended target was not damaged;
   - continue with the corrected target, not the accidental one.
4. For money/trading skills, never equate “skill exists/guardrail passes” with “live trading enabled.” Use readiness labels:
   - `skill-scaffold/read-only-prep`;
   - `guarded-live-rail-disabled`;
   - `live-enabled-with-readback` only after exact approval, wallet/secrets, geofence, execution, and readback.
5. If the corrected task is “make it trade through chip-privy,” create/execute a SuperGoal for the runtime rail in the relevant repo, not another skill-only scaffold.

## Quick self-check

Before finalizing a launch pack, answer internally:

- What exact user phrase am I obeying?
- What was the reply target?
- What existing artifact/skill/repo is the target?
- Am I solving the user’s intended task, or a bug I personally caused?
- Is my readiness wording honest, especially for money movement?

If any answer is unclear and the ambiguity changes the target artifact, ask one compact clarification instead of writing the wrong SuperGoal.
