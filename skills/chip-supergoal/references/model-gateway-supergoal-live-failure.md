# Fusion/model-gateway SuperGoal live-failure lesson

Use this reference when planning or executing a SuperGoal for a model router, gateway, bot model, multi-model fusion/MoA route, or other agentic model surface where direct API success can differ from real user-visible behavior.

## Trigger signals

- the user/operator says a previous fix is “фикция”, “not a fix”, “works like shit”, or demands “works like clocks”.
- A direct route smoke passed but Telegram/bot/gateway behavior still failed.
- A deterministic plugin or special-case handler made one prompt work while the model/router remains broken.
- The failure involves tool loops, progress-only answers, billing/credits on second calls, provider quorum, or model alias routing.

## Planning rule

Do not treat route existence, unit tests, or direct smoke as proof that an agent model works. A model/gateway SuperGoal needs phase-specific acceptance criteria that cover:

1. exact incident reproduction fixtures;
2. model contract and allowed surfaces;
3. tool-call and `role=tool` continuation behavior;
4. stage-level billing/credits and zero-debit failure audit;
5. plugin/fallback masking detection;
6. real runtime route smoke with usage/log proof;
7. exact bot/user-visible readback when the reported failure was in Telegram or another channel.

## Acceptance-criteria pitfall

Generic criteria such as “report exists”, “tests pass”, or “RPD review ran” are not enough for model/gateway work. They allow the same false completion pattern: green local checks while the real bot path remains broken.

Each phase must have falsifiable checks tied to the failure class. Examples:

- `quorum_502`: aggregator must not run when `min_panel_success` is unmet; response is a structured failure; success debit is zero.
- `progress_only_final`: “секунду/получаю/загружаю” cannot be a final answer for live-data/tool-worthy prompts.
- `role_tool_continuation`: assistant tool calls and `role=tool` messages are converted into bounded context before proposer/judge/final-writer calls.
- `insufficient_credits_second_call`: second/tool-result continuation failures are visibly `denied_insufficient_credits`, not misdiagnosed as model hang.
- `plugin_masked_smoke`: final E2E proof is invalid until fallback plugins/special handlers are disabled, narrowed, or proven not to handle the test prompt.

## Execution rule

If the user asks for `/rpd xhigh` or criticizes the plan before `/goal` starts, mutate the SuperGoal artifacts before continuing:

- write a report under `.supergoal/reports/`;
- replace generic phase acceptance criteria with phase-specific criteria;
- upgrade risky contract/judge/cleanup phases to `RPD required: yes`;
- make production/bot side effects explicit approval gates;
- re-run `validate-phase.sh` on every touched phase.

## Cleanup/no-crutches rule

Before rollout or final proof phases, actively remove ambiguity from the SuperGoal workspace:

- quarantine source/test backup files such as `src/**.bak-*`, `tests/**.bak-*`, `.orig`, `.rej`, and editor backups under `.supergoal/evidence/<phase>/` with a manifest and hashes;
- archive stale root reports from invalidated previous runs under `.supergoal/archive/<reason>-<timestamp>/` so later phases cannot consume old approval gates, old final audits, or old bot-switch reports as active truth;
- add/update a project reference that says deterministic plugins/fallbacks are incident mitigations, not model proof;
- add tests or probes that reject public/default/model-switch claims when the current eval classification is `specialized-only` or `blocked`;
- extend secret scans when model/bot work introduces new leak classes, especially Bearer examples, BotFather token shapes, raw customer keys, and Telegram `-100...` chat ids.

This cleanup is not cosmetic. Stale reports and backup files can make a future operator believe production/bot approval already happened when it belonged to an invalidated run.

## Final proof rule

For model/gateway/bot work, “fixed” requires evidence from the highest surface that failed. If Telegram failed, direct API success is only partial proof. Final report must say one of:

- live Telegram/readback passed with exact message ids and logs/usage rows;
- live Telegram/readback was approval-blocked and not claimed fixed;
- local/model hardening passed but production/bot rollout remains gated.

## Default-ok vs narrow-proof pitfall

A narrow marker smoke or hidden complex DM test can prove that a route is reachable, but it does **not** prove the model is safe as a live/default bot model. If the route is left as a public/default model, the SuperGoal must continue until normal user-visible prompts on the same surface pass under realistic credit/context/tool conditions.

Required hardening when a live default fails after narrow proof:

1. Archive the previous `COMPLETE` run as invalidated evidence and start/revise a SuperGoal whose first phase freezes the incident baseline.
2. Treat topups as temporary verification fuel, not as the fix. The fix must prevent surprise mid-turn `denied_insufficient_credits` or expose a deliberate low-credit UX before tool work starts.
3. Do not include the expected answer in all live proof prompts. Use at least one hidden-answer fixture where the expected output is stored outside the Telegram prompt, otherwise the test can pass by copying.
4. Replay the actual failed surface: same bot, same group/DM class, same reply/readback mechanics, and the same style of user prompt. A DM marker test is insufficient for a group failure.
5. Require ledger rows to prove the accepted path: `endpoint_family`, stage/tool-planner rows, final aggregator rows, cost/debit, and absence of mid-turn credit denial.
6. If the model remains `specialized-only`, do not leave it as the live/default bot model and do not call the run complete.

## Default-ok / hidden-proof addendum

When the user/operator asks to make a fusion/model route “100% working” or `default-ok`, do not interpret this as “make one smoke pass” or “avoid the route by rolling back to the old default.” Rollback remains a safety rail and rollback target, not the answer to a task whose goal is to make the new route actually work. The plan must explicitly separate:

- `specialized-only` — narrow/internal route can pass selected tasks but is not safe as a public/default bot model;
- `default-ok` — normal user-visible bot/group tasks complete reliably inside a known cost envelope.

For default-ok proof:

1. Reproduce the exact live failure surface first. If the screenshot/failure happened in a Telegram group/topic, DM tests and direct API tests are supporting evidence only.
2. Hidden complex tests must keep expected answers out of the user prompt. Store fixtures/expected values out-of-band and ask the bot to compute them.
3. Credit topups are not fixes. If the route can spend tool/planner/proposer stages and then fail final answer with `denied_insufficient_credits`, add a phase for credit reservation / whole-turn preflight / readable low-credit fail-before-tools semantics.
4. A previous `COMPLETE` is invalidated when live readback falsifies it. Archive the old `.supergoal` run as evidence and start a new SuperGoal from phase 0 rather than patching over the old completed state.
5. Final audit must check both the economic envelope and the highest-surface readback: Telegram message ids, `reply_to`, exact reply text, `usage_events` rows, no plugin/fallback handling, and no generic provider-failed text.