# Legacy monolith snapshot — 2026-06-19

This file preserves the pre-refactor `SKILL.md` verbatim so incident lessons are not lost while the live root becomes a Principal+ controller. Load it only for archaeology/backward-compat investigation; new behavior belongs in canonical references and templates.

---

---
name: chip-supergoal
description: Plan-only autonomous software task planner that writes ROADMAP, phase specs, protocol, and one `/goal` handoff. Embeds RPD review gates directly; does not depend on an external `/rpd` skill. Use for `/chip-supergoal`, “plan and ship X”, autonomous build planning, or non-trivial feature/refactor/redesign that should be handed off to `/goal` for execution.
argument-hint: <describe what you want built, fixed, or shipped>
---

# chip-supergoal

## Stale phase-plan route verification

When executing a phase file that names an existing route/module/page, verify current code before treating the phase wording as canonical. Phase files can lag behind app reality. If a route is now redirect-only/deprecated, preserve the phase intent but implement against the current canonical source and document the correction. See `references/stale-phase-route-verification.md`.

When executing a phase that edits or validates a Hermes skill, resolve the live `skill_dir` before trusting hard-coded paths in the phase spec. User-local skills may be category-backed (`~/.hermes/skills/chip/<name>`), while stale phase specs may point to `~/.hermes/skills/<name>`. Patch the phase command to the live path, avoid duplicate skill creation, and remove validation residue such as `scripts/__pycache__/`. See `references/category-backed-skill-path-validation.md`.

## Standing-goal continuation rule

If Chip sends a standing-goal continuation message for an existing Supergoal, continue from `STATE.md` current phase immediately. Do not ask him to send `/goal` again, do not explain the old button/reply mechanics again, and do not restart from phase 0 unless `STATE.md` says so. Preserve the visible transcript contract: every numbered phase must print `SUPERGOAL_PHASE_START`, `SUPERGOAL_PHASE_VERIFY`, `SUPERGOAL_PHASE_DONE`, then `SUPERGOAL_TURN_YIELD`; final completion still requires `AUDIT_COMPLETE` before `SUPERGOAL_RUN_COMPLETE`.

## Mandatory Telegram file delivery for Chip SuperGoals

When the user/operator asks for files, says final files must be sent in Telegram, or asks for a SuperGoal/ТЗ package, make delivery scripted and blocking, not a promise.

- The planning-stage Telegram review pack is **exactly three native `.md` files by default**: `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`. Send them as real `MEDIA:` attachments before asking Chip to start/dispatch the goal. Do not substitute an archive or paste-only text.
- Add a `.supergoal/scripts/send-review-md-files.*` gate and require `.supergoal/out/review-md-files-delivery-receipt.json` with `ok=true` and `sent=true` before launch/dispatch. The script must be idempotent: if the same files/hash+target already have a sent receipt, it exits cleanly without resending. Use `SUPERGOAL_FORCE_RESEND=1` only after Chip explicitly asks for a resend.
- Add a `.supergoal/scripts/package-final-artifacts.*` and `.supergoal/scripts/send-final-artifacts.*` gate, require `.supergoal/out/final-artifacts-delivery-receipt.json` with `ok=true` and `sent=true`, and include `SUPERGOAL_FILES_SENT` before `AUDIT_COMPLETE` / `SUPERGOAL_RUN_COMPLETE`. Final artifact delivery must also be idempotent by archive hash+target; do not resend on retries unless Chip explicitly asks.
- Patch `LAUNCH_GOAL.md`, `PROTOCOL.md`, final phase acceptance criteria, and validation scripts so the SuperGoal cannot be marked complete if the required files were not actually sent to the intended Telegram topic.
- Do not combine manual `send_message` delivery with a script retry. If a manual fallback is unavoidable, immediately write the same success receipt (`manual_fallback=true`, files/hash+target) and do not run the delivery script again for the same artifacts.
- Also attach/send the current handoff files as real media when the user says they do not see files.

### Bounded no-internal-approval runs

When the operator explicitly says to run a SuperGoal “до конца без апрувалов внутри” / “no internal approval prompts”, treat that as permission only for the manifest-covered safe lane, not as broad consent. Encode the lane as machine-checkable artifacts early in the run:

- Write or verify an exact bounded manifest: expected wallet/account/entity IDs, allowed read-only/setup-verification actions, forbidden money/mutation actions, max fee/cap values, and `noInternalApprovalPrompt=true` terminal-blocker semantics.
- First phase should freeze/verify the money surface with read-only probes: balances/holds, open orders, fills, approved builders, extra agents, and exact returned IDs. Never substitute chat claims for live/readback evidence when the endpoint is accessible.
- Add a reusable manifest validator and negative tests: wrong wallet/entity, wrong builder, wrong agent, higher fee, forbidden trade/withdraw/order/cancel/close/funding/leverage/margin/new-approval action. Forbidden/outside-manifest actions fail closed; do not generate another approval card.
- Add a live readback reconciler before operator convenience scripts: distinguish each source (Pear `/agentWallet`, Hyperliquid `extraAgents`, `approvedBuilders`, `maxBuilderFee`, balances, open orders/fills), classify endpoint/API drift explicitly, and keep `mutationAllowed=false` in every evidence component.
- Add runtime hardening before any operator command: a catalog of forbidden runtime surfaces that remains denied even when live credentials are present; approval packages may stay preview-only, but execution stays disabled unless a later explicit trading envelope exists.
- If adding a one-command operator script, it must call the same runtime hardening + manifest validator + read-only live reconciliation, produce compact redacted output, write a report, exit non-zero on drift/forbidden attempts, and prove forbidden attempts stop before network with no approval prompt.
- Phase reports should include command exits, criteria map, redacted live evidence, negative-case evidence, and the usual `RPD_PHASE_REVIEW` before `SUPERGOAL_PHASE_DONE`. This avoids approval-card spam while keeping consent bounded and auditable.

When the operator explicitly says “без апрувалов внутри” / “no internal approval prompts”, treat the standing-goal wrapper as the single bounded approval for manifest-covered work. Do not use `clarify` or approval cards between phases. Execute only the actions allowed by the launch manifest; if a needed action is outside manifest, fail closed in the phase report and stop rather than asking for a micro-approval. Update `STATE.md` only after the phase’s required commands, evidence files, and RPD gate are complete; the next continuation should start from the next phase, not repeat the completed one. For the checklist and pitfalls, see `references/no-internal-approval-standing-goal.md`.

If `STATE.md` still says `READY_FOR_REVIEW` / waiting for start because the launch-card or `clarify` timed out, but the latest inbound message is a real `[Continuing toward your standing goal]` wrapper containing the exact goal body, treat that wrapper as explicit start confirmation. Run the next phase from disk, update `STATE.md` to `IN_PROGRESS` when the phase completes, and do not re-open the launch-card or ask for `▶ Start Goal` again. The visible GoalManager wrapper is stronger evidence than stale planner-state text.

If `STATE.md` already says `Status: COMPLETE` / `Current phase: COMPLETE`, the standing goal is done. Do not re-run phase checks, final audit, deploy smoke, or repeated git/status probes unless the user asks for a fresh verification. Reply with a short completion stop only on the first post-complete continuation: cite `STATE.md`, the final audit timestamp/SHA if already visible, say there is no next numbered phase, and stop. If `AUDIT_COMPLETE` is already recorded on disk but the visible conversation/handoff says `SUPERGOAL_RUN_COMPLETE` has not yet been emitted, do a bookkeeping-only final reply: print `AUDIT_COMPLETE` summary from the saved report plus `SUPERGOAL_RUN_COMPLETE`, then stop. Do not treat this as a reason to rerun the audit. Repeated host continuations after completion are evaluator leftovers, not new work; after the first visible stop, identical repeats in the same group chat should be suppressed if the runtime supports true no-delivery. If a visible final message is still required, do not return an empty string; use `COMPLETE — no-op.` to avoid `(empty)` spam. Only answer substantively if Chip adds a new question, asks for fresh verification, reports a bug, or provides a new goal. See `references/repeated-complete-continuations.md`.

If a continuation points at a completed root but the visible recent conversation created or corrected a newer successor SuperGoal for the same domain (for example old fail-closed rail complete, new live-activation rail ready at phase 0), do not keep replying `COMPLETE`. Treat it as a stale/misrouted GoalManager wrapper: state `STALE_GOAL_WRAPPER`, name the completed root and the newer active root, and switch only if the wrapper/user explicitly names the newer root or the latest user message asks to continue the new goal. Do not infer execution authority for money/live-mutation phases from the stale wrapper.

If `STATE.md` says `READY_FOR_DELETE_APPROVAL`, `BLOCKED_BY_APPROVAL`, or an equivalent human/provider approval gate, treat repeated standing-goal continuations the same way: do not re-read the same unchanged files, do not re-run provider/DNS/backup probes, and do not keep restating the full menu. Give one concise stop that names the exact missing approval and the allowed choices. If the user keeps sending identical continuation wrappers, answer even shorter: `READY_FOR_DELETE_APPROVAL — blocked pending explicit Chip choice: retain / delete / snapshot-then-delete.` For `BLOCKED_BY_APPROVAL`, after the first full blocker handoff, do not paste the entire approval card on every repeated wrapper; use a one-line stop with the blocker label and the shortest missing approval name, and only reprint the exact phrase if the user asks or the previous visible reply did not contain it. **If the visible `/goal` loop keeps auto-continuing on the same `BLOCKED_BY_APPROVAL` state, this is a GoalManager control-plane bug, not more SuperGoal work: mark/leave the goal blocked or paused, do not burn turns repeating the phrase, and patch the GoalManager/skill protocol so approval blockers terminate auto-continuation.** Only use `clarify` once when it materially improves UX; after timeout, stop instead of polling again.

For approval-gated production phases, a `/goal` start or standing-goal continuation is **not** approval for deploy/restart/env/bot changes. The executor may perform read-only preflight, write an approval-gate report, and set `STATE.md` to `BLOCKED_BY_APPROVAL`; it must not print `SUPERGOAL_PHASE_DONE` / `SUPERGOAL_TURN_YIELD` unless the phase spec explicitly defines the approval gate itself as the phase deliverable. Production rollout phases are incomplete until approved live-smoke acceptance criteria pass.

Exception: if the latest user message asks to review the conversation and update the skill library, stop the active Supergoal loop for that turn. Treat the skill-update request as the current task, save durable project/process learnings into the relevant class-level skill or its `references/` files, and do not emit Supergoal phase markers until the user/host sends the next standing-goal continuation.

You are running the chip-supergoal planner.

$ARGUMENTS

Your job: **plan deeply, write execution artifacts, and hand off one `/goal`**. `/chip-supergoal` is plan-only; the future `/goal` session performs execution from the generated files.

## What "every aspect is perfect" means here

The user's bar is high. Translate it into measurable criteria, not vibes:

- **Functional** — the feature works for the golden path and the obvious edge cases
- **Engineering** — build, typecheck, lint, tests all pass; no new warnings
- **Polish** — UX/copy, error states, empty states, loading states are handled
- **Hardening** — security review, input validation, no obvious regressions
- **Verification** — every phase produces transcript evidence the evaluator can see

If a phase can't be measured, it isn't a phase. Rewrite it until it can.

## How this skill works (one-shot summary)

0. **Available context** — preload memory; detect available tools (Context7, WebSearch, MCPs, skills); resume any in-progress chip-supergoal state
1. **Intake** — restate, classify, ask enough questions to cover every material gap. Greenfield walks the full category checklist (platform, stack, design direction, integrations, scope, audience, perf, data model) in batches of up to 4 until everything material is filled in; brownfield asks 0–2 since recon answers most structural questions.
2. **Recon** — parallel codebase + environment scan
3. **Research-before-design + Architect+ lite** — if research is required, load/use skill `perplex` first when available; run fresh-docs/existing-solutions gates; for substantial risky plans add source-of-truth, permissions, failure modes, and verification strategy; then list top-3 risks + dependencies
4. **Decompose** — derive phase count from the task itself; no fixed cap
5. **Write phase specs** — one work-spec file per phase under `.supergoal/phases/phase-N.md` (any length, no char budget)
6. **Embedded RPD plan review** — run RPD_PLAN_REVIEW, mutate weak specs in place, then show summary + concrete revision menu; wait for explicit go/no-go
7. **Hand off one ready-to-paste `/goal`** with a short end-state condition; the user pastes once, and the agent inside that fresh `/goal` session executes phases sequentially with retry + fix-spec recovery + per-phase memory writeback, then runs a **final audit** that re-verifies the work against the original ROADMAP and self-heals any gaps before completion holds

Two human gates only: **clarifying questions for true gaps (Stage 1)** and **plan review (Stage 6)**. Everything else runs autonomously.

### Why one `/goal`, not a chain

`/goal` in both Claude Code and Codex takes a **short end-state condition**, not a long task body. A fast evaluator checks the condition against the transcript after each turn and auto-continues until it holds. chip-supergoal leverages this directly: one `/goal` covers the whole run; phase work lives in files the agent reads from disk; the condition is "all phases done, `SUPERGOAL_RUN_COMPLETE` printed." The executor runs **one numbered phase per assistant turn**, prints `SUPERGOAL_TURN_YIELD`, and lets `/goal` continue to the next phase. This preserves the single-goal model while preventing long production runs from hitting per-turn tool-call/context limits. No char budget, no inter-session chain dispatch, no fragility.

## Locate the skill directory

```bash
SUPERGOAL_DIR=$(dirname "$(ls -1 \
  "$HOME/.hermes/skills/chip-supergoal/SKILL.md" \
  "$HOME/.claude/skills/chip-supergoal/SKILL.md" \
  "$PWD/.claude/skills/chip-supergoal/SKILL.md" \
  2>/dev/null | head -n1)")
export SUPERGOAL_DIR
export SUPERGOAL_ROOT="${SUPERGOAL_ROOT:-.supergoal}"
mkdir -p "$SUPERGOAL_ROOT/goals"
echo "SUPERGOAL_DIR=$SUPERGOAL_DIR"
echo "SUPERGOAL_ROOT=$SUPERGOAL_ROOT"
```

All artifacts live under `$SUPERGOAL_ROOT`. Skill assets (scripts, references, templates) live under `$SUPERGOAL_DIR`.

---

## Stage 0 — Available context (memory + tools)

Before doing anything else, sense what's available this session. This is what makes the run frictionless — if memory already knows the user's preferences, don't ask; if a tool isn't available, don't try to call it.

### Memory preload

```bash
# Detect a memory directory. Common locations:
MEM_DIR=""
for cand in \
  "$HOME/.claude/projects/-Users-$(whoami)/memory" \
  "$HOME/.claude/memory" \
  "$PWD/.claude/memory" \
  "$SUPERGOAL_ROOT/memory"; do
  [[ -d "$cand" ]] && MEM_DIR="$cand" && break
done
echo "MEM_DIR=$MEM_DIR"

if [[ -n "$MEM_DIR" && -f "$MEM_DIR/MEMORY.md" ]]; then
  echo "--- MEMORY INDEX ---"
  cat "$MEM_DIR/MEMORY.md"
fi
```

Read the index. Then **selectively** read individual memory files that look relevant to the task (feedback memories about the stack/domain, user role memories, related project memories). Don't dump them all into context — pull what matters.

Capture applicable memory hits in `$SUPERGOAL_ROOT/applied-memories.md` (one line per memory: name, why-applicable, what-it-changes). Surface them in Stage 1 as "Applied from memory: …" so the user can see what's being inherited and correct anything stale.

### Tool discovery

Tools differ between sessions and hosts (Claude Code vs Codex, different MCP server sets). Detect, don't assume:

- **Context7** — available if `mcp__claude_ai_Context7__resolve-library-id` or similar is in the tool list. If absent, skip it; rely on training-cutoff knowledge + WebSearch if that's present.
- **WebSearch / WebFetch** — available if listed. If neither, skip web research.
- **Project skills** — check the available-skills list for domain-relevant skills (e.g. `mobile-ios-design`, `clerk-auth`, `expo-dev-client`) and note them in `$SUPERGOAL_ROOT/applied-skills.md` to invoke from inside phase goals if relevant.
- **Prior chip-supergoal state** — if `$SUPERGOAL_ROOT/STATE.md` exists from a previous run, read it; resume rather than restart.

Write detected tools to `$SUPERGOAL_ROOT/tools.md`. Stage 3 and the phase goals reference this file when deciding what to invoke.

### Resume detection

If `STATE.md` exists and shows `Status: IN_PROGRESS` with a phase pending, **do not re-plan**. Print a one-line "Resuming chip-supergoal from phase N" and jump straight to Stage 6 (plan review) with the existing artifacts, or directly to Stage 7 (dispatch) if the user confirms resume.

---

## Stage 1 — Intake & clarifying questions

Echo the task back in **one sentence**. Then classify it (tags can combine):

| Tag | Trigger |
|---|---|
| `greenfield` | Request implies a new project; cwd has no `.git/` or empty tree |
| `brownfield` | Change in an existing repo |
| `bugfix` | Mentions "bug", "broken", "fails", "regression" |
| `refactor` | Mentions "refactor", "clean up", "restructure" |
| `ui` | Mentions "design", "polish", "UI", "UX", "responsive", "redesign" |

**Calibrate the question count to the context.** Greenfield has no codebase to scan, so it needs enough verbal context to plan well — never artificially limit questions when material info is missing. Brownfield runs lean on recon, so questions are sparse.

### Greenfield — gather enough context to plan well

A new project has no signal beyond the user's prompt + memory. The planner's job in Stage 1 is to **enumerate every category that meaningfully shapes the plan, eliminate the ones already answered by memory or prompt, and ask about every remaining one**. Don't stop until every material gap is filled.

**Category checklist — work through this for every greenfield run:**

| Category | Why it shapes the plan |
|---|---|
| **Target platform / surface** | iOS, Android, web, desktop, CLI, multi — the biggest fork. Different stacks, different phases. |
| **Stack / framework preference** | Next.js vs SvelteKit, Expo vs bare RN, FastAPI vs Django, Swift vs SwiftUI vs UIKit, etc. Affects every phase. |
| **Design direction / aesthetic** | Minimal-mono, brutalist, glass morphism, Apple-native, dashboardy-corporate, retro, etc. Determines tokens, component shapes, Polish phase content. |
| **Integration anchors** | Auth provider, database, payments, hosting, analytics, file storage, email — anything that locks in a vendor up front. |
| **Scope cut-line** | MVP-this-week vs full feature; what's explicitly out of scope vs deferred to v2. |
| **Primary use case / audience** | Solo-dev tool, team SaaS, public consumer app, internal admin — drives auth flow, onboarding shape, error tolerance. |
| **Performance / scale constraints** | "Realtime sub-100ms" vs "background batch ok"; expected traffic; offline-first or online-only. Only ask if non-trivial. |
| **Data model anchors** | If the prompt implies data, ask the shape ("users + posts? users + projects + tasks?"). Only if not obvious. |

**Process:**

1. For each category, ask: *did the user's prompt mention it? Does memory have a relevant preference?*
2. If yes → use that, surface as "Applied from memory: …" or "From your prompt: …"
3. If no → that category becomes a question.
4. Ask all remaining questions in **batches of up to 4** (the `AskUserQuestion` tool ceiling) until every material gap is filled. Two batches is fine for greenfield; three is rare but allowed if a complex task genuinely warrants it.
5. Within each batch, lead with the highest-leverage choices (the ones that change the phase shape most).

**Anti-patterns:**

- **Don't ask one batch and then plan around silent assumptions for the rest.** If you're about to assume the design direction, the auth provider, AND the scope cut-line, that's 3 assumptions and one batch of follow-up is cheaper than getting it wrong.
- **Don't pad questions when memory/prompt already covers them.** Reading "I want a SwiftUI iOS app with Liquid Glass" → don't ask "what platform?", "what stack?", or "what aesthetic?". Just ask about integrations, scope, and use case.
- **Don't ask micro-details** that belong in plan review: naming, file paths, copy wording, color palette specifics, library minor versions, default test framework if the stack has one. Those go into ROADMAP.md as assumptions and surface in Stage 6's revision menu.

### Brownfield — 0–2 questions, one batch

The codebase plus recon scripts already answer most structural questions (stack, package manager, build/test/lint, conventions, what exists). Ask only for **true gaps** memory + prompt + recon leave open:

- Scope cut-line ("just this surface, or also touch the related ones?")
- Compatibility surface ("backwards compat with the old API path, or break it?")
- Primary fork when ambiguous ("which of these two existing patterns do you want me to extend?")

Most well-described brownfield tasks ask **zero questions**.

### In both modes

1. Lead with "Applied from memory: …" and "From your prompt: …" so the user sees what's being inherited or read off before answering.
2. Each `AskUserQuestion` batch caps at 4 (tool limit). Greenfield can use multiple sequential batches; brownfield is one batch max.
3. If you genuinely need zero questions, say "No clarifying questions — proceeding from prompt + memory + recon." and move straight to Stage 2.
4. Never ask about anything you can responsibly assume — those go into the Stage 6 plan review for one-click correction.

---

## Stage 2 — Recon (parallel)

Run recon scripts in parallel. They populate context files under `$SUPERGOAL_ROOT/`.

### Brownfield path

```bash
bash "$SUPERGOAL_DIR/scripts/detect-stack.sh"   > "$SUPERGOAL_ROOT/context.md"
bash "$SUPERGOAL_DIR/scripts/summarize-repo.sh" > "$SUPERGOAL_ROOT/repo-map.md"
```

### Greenfield path

```bash
bash "$SUPERGOAL_DIR/scripts/detect-env.sh" > "$SUPERGOAL_ROOT/context.md"
```

Read the outputs. Then print a **5-line summary** to the user: stack, package manager, build/test/lint commands, notable modules (if any), risky areas. This is what tells them you've actually understood their codebase before planning.

---

## Stage 3 — Deep think

This is the difference between a generic plan and a strong chip-supergoal plan. Spend real cycles here — but use only what's available.

**Required regardless of tools:**
- Identify the **top 3 risks**: what's most likely to go wrong, what's hardest to undo, what's easy to miss until shipped.
- Identify **non-obvious dependencies**: things that have to happen in a specific order or block other work.
- Apply memory hits from `$SUPERGOAL_ROOT/applied-memories.md` — bake them into goals, constraints, or risk mitigations.

**Research-before-design gates** (use only when they materially shape the plan):

- **Research trigger check** — required for greenfield products/systems, unfamiliar domains, current SDK/API/framework behavior, auth/payments/security/compliance, major migration/refactor, build-vs-buy uncertainty, or when the user asks for best practices / world practice / not reinventing the wheel.
- **Skill-first Perplex gate** — if skill `perplex` exists in the available skills catalog, load/use that skill first for open-ended current web research. Do not treat generic `web_search` as equivalent just because a runtime backend might route through Perplexity.
- **Fresh-docs gate** — use Context7 or official docs for SDK/API/framework-specific facts; if unavailable, record the version assumption and add a phase-1 verification criterion.
- **Existing Solutions Gate** — search OSS, SaaS, package registries, libraries, and reference-code. Shortlist candidates, inspect top candidates statically only, and decide `buy | wrap | fork | copy_pattern | build_fresh | defer`.
- **Fallback search** — use generic `web_search` / `web_extract` only when no dedicated research skill/tool is available or when fetching known source URLs.
- **Fail-closed for critical facts** — if missing external facts materially affect architecture, security, compliance, payments, auth, SDK/API choice, or build-vs-buy, the plan is not approval-ready until research is completed or the user explicitly accepts the assumption.

**Write `$SUPERGOAL_ROOT/THINKING.md`** with sections: Goals, Constraints, Risks, Dependencies, Open Questions (already-assumed), Memory hits applied, Tools/skills relied on, Best Practices Applied. Keep it tight — 1–2 pages. This is the substrate the roadmap derives from. If any research gate ran, also write `$SUPERGOAL_ROOT/RESEARCH.md` from `$SUPERGOAL_DIR/templates/RESEARCH.md` and fill status, sources, existing-solution candidates, build-vs-buy verdict, planning implications, and unverified assumptions.

See `references/planning-depth.md` for the bar to clear here.

---

## Research tool priority — skill-first

When current research is required, prioritize the **`perplex` skill itself** if it exists in the available skills catalog. This means: load/use the `perplex` skill workflow first, before generic web search, because local installs may encode the operator's preferred Sonar routing, source mix, credentials, cost controls, and fallback rules.

Priority order:

1. **Skill `perplex`** — if available, load it and follow its workflow for open-ended web/current research.
2. Context7 / official docs — for SDK, framework, API, and version-specific planning facts.
3. GitHub/package/SaaS search — for existing solutions, libraries, products, and reference-code candidates.
4. Generic `web_search` / `web_extract` — fallback only when no dedicated research skill/tool is available or when fetching known source URLs.
5. If critical facts are unavailable, mark the plan `not approval-ready` unless the user explicitly accepts the assumption.

Do not collapse this into “use any Perplexity backend.” The trigger is the installed `perplex` skill and its local workflow, not merely a search provider name.

---

## Architect+ lite gate

For large, architecture-affecting, agent-native, security-sensitive, or production-facing plans, add an Architect+ lite block before decomposition. Do not drag in a full formal-contract system for small work; use this only when the plan would otherwise be easy to execute incorrectly.

Architect+ lite requires:

- **Source-of-truth boundary** — what owns truth, what is derived/cache/view state, and what must not become a second source of truth.
- **Permission matrix** — human, service, admin, and agent roles; what each can read/write/execute.
- **Failure-mode matrix** — top failure modes mapped to continue/degraded/fail-closed/human-gate behavior.
- **Verification strategy** — commands, tests, probes, smoke checks, logs, screenshots, or API checks that prove the riskiest behavior before implementation is considered done.

Write the result into `THINKING.md` and summarize it in `ROADMAP.md` / Stage 6 when applicable. If skipped, record a narrow reason.

---

## Embedded RPD v2 review system

chip-supergoal is independent from any external `/rpd` skill. Do not load or invoke another RPD skill to run this workflow. Use the embedded hard contract in `references/rpd-review-gates.md`.

RPD v2 is a mutation gate, not a commentary layer:

- Every RPD finding must either mutate `ROADMAP.md`, `THINKING.md`, a phase spec, mandatory commands, acceptance criteria, or an audit-fix spec.
- Or it must be marked `checked-holds` with an evidence tier.
- Free-floating concerns, persona essays, and ungrounded “senior vibes” are invalid.
- Material claims must mark evidence strength: `direct artifact`, `provided context`, `external/current source`, or `assumption` with a falsifier.

RPD runs in two places owned by this planner and one place delegated to the generated `/goal` protocol:

1. **Inside `/chip-supergoal`:** `RPD_PLAN_REVIEW` always runs after roadmap/phase specs are written and before the plan is shown to the user.
2. **Inside generated `/goal`:** `RPD_PHASE_REVIEW` runs only for phase specs marked `RPD required: yes` or risky phases.
3. **Inside generated `/goal`:** `RPD_FINAL_REVIEW` always runs after `AUDIT_VERIFY` and before `AUDIT_COMPLETE`.

Risky phases include auth, authorization, payments, secrets, private data, database migrations, destructive data changes, production infra, gateway/routing/cron/model-provider routing, architecture/migration, recurring bugs, baseline-red recovery, public launches, and claimed completion after a risky run.

For risky plans/phases/final audit, run the embedded **Senior Gate** from `references/rpd-review-gates.md`: severity `P0/P1/P2/P3`, evidence ledger, elegance/effectiveness, overengineering, and principal-review checks. Any new layer/fallback/agent/shim must pass the overengineering budget: necessity, simpler alternative rejected, removal condition.

---

## Stage 4 — Decompose into phases

Break the work into **as many phases as the task actually needs** — no fixed count, no upper or lower cap. The right number falls out of the work itself: how many independently verifiable units exist between empty repo (or current state) and "done perfectly." A trivial change might need 2 phases; a typical feature 4–6; a full-stack greenfield app 8–12; a major migration 15+. Read `references/phase-design.md` for how to slice well — the short version:

- Each phase delivers something **verifiable on its own** (it builds, it passes its own tests, you could ship it as a partial increment)
- Phases have **explicit dependencies** (phase 3 depends on 1 and 2)
- The **last phase is always a "Polish & Harden" phase** covering edge cases, error states, security, accessibility, copy, perf — this is how "every aspect is perfect" gets enforced
- For UI work, include a dedicated **visual polish** phase with screenshot/visual evidence requirements
- For brownfield, include an early **safety net** phase if test coverage is thin (add characterization tests before changing behavior)

Each phase has:
- **Name** (5 words max, action-first: "Build auth foundation")
- **Why** (1 sentence)
- **Deliverables** (concrete files/features that will exist when done)
- **Acceptance criteria** (5–10 measurable items)
- **Mandatory commands** (build, typecheck, lint, test that must pass)
- **Evidence required** (what the agent must print into the transcript to prove completion)
- **Dependencies** (which prior phases must be done)

---

## Stage 5 — Write the roadmap and phase specs

Core files, all under `$SUPERGOAL_ROOT/`:

1. **`ROADMAP.md`** — the plan (template at `$SUPERGOAL_DIR/templates/ROADMAP.md`).
2. **`STATE.md`** — live progress file the executor updates per phase (template at `$SUPERGOAL_DIR/templates/STATE.md`).
3. **`RESEARCH.md`** — required when Stage 3 research gates run (template at `$SUPERGOAL_DIR/templates/RESEARCH.md`); omit only with a narrow skipped reason in `THINKING.md` and `ROADMAP.md`.
4. **`phases/phase-N.md`** — one work-spec file per phase (template at `$SUPERGOAL_DIR/templates/phase-goal.txt`, renamed conceptually to "phase spec"). **Any length** — these are read from disk by the executor, not passed to `/goal`, so no char budget.

Each phase spec must include these markers and sections so the agent and evaluator both have stable anchors. `scripts/validate-phase.sh` also requires a `## Work` section, so include it explicitly in every generated phase file:

```
SUPERGOAL_PHASE_START
Phase: <N> of <total> — <name>
Task: <one-line>
Mandatory commands: <list>
Acceptance criteria: <count>
Evidence required: <list>
Depends on phases: <list or "none">
RPD required: yes|no
RPD focus: <security|integration|ux|migration|data-loss|gateway|payments|none>

## Work
<exact execution instructions for this phase>

## Acceptance criteria
<measurable criteria>

## Mandatory commands
<commands>

## Evidence required
<evidence>

[Agent will print SUPERGOAL_PHASE_VERIFY, SUPERGOAL_PHASE_DONE, and SUPERGOAL_TURN_YIELD here during execution]
```

Validate each spec with `bash $SUPERGOAL_DIR/scripts/validate-phase.sh .supergoal/phases/phase-N.md` — it confirms the required markers exist. No char budget.

---

## Stage 6 — Plan review & confirmation (hard gate)

Before any `/goal` is dispatched, show the user the full plan and **ask for explicit confirmation**. The chain runs unsupervised once it starts, so this is the last cheap moment to correct course. Skipping this step is a bug.

### Stage 6a — Embedded RPD plan review (runs once, mutates specs)

Plan-time is the cheapest moment to catch expensive bugs. Run one embedded `RPD_PLAN_REVIEW` using the contract in `references/rpd-review-gates.md`.

Use the four personas in sequence, inheriting findings:

1. **Pattern Hunter** — is this a known pattern or repeat failure class?
2. **Gonzo Truth-Seeker** — what unverified assumption is the plan treating as fact?
3. **Devil's Advocate** — how does this plan fail concretely?
4. **Integrator** — what files, services, configs, docs, and user-visible flows can go split-brain?

Mandatory checks:

- Every acceptance criterion must be falsifiable and mapped to a verification/evidence path.
- Each material claim must carry an evidence tier: `direct artifact`, `provided context`, `external/current source`, or `assumption`.
- Each phase must be independently verifiable.
- Risky phases must declare `RPD required: yes` and a focused `RPD focus` value.
- The weakest dependency chain must have a mitigation.
- Any unverified assumption that materially changes the plan must become either a user question, a mandatory command, an acceptance criterion, or a research gate.
- The canonical source of truth must be identified for project state, runtime/config, generated SuperGoal artifacts, and docs.
- New layers/fallbacks/agents/shims must pass the overengineering budget: necessity, simpler alternative rejected, removal condition.
- Risky production/money/privacy/gateway/architecture plans must run Senior Gate with severity `P0/P1/P2/P3` and an evidence ledger.
- If subagents are used, each must return a context receipt; stale/partial subagent findings cannot be treated as direct evidence.

**Mutation rule:** every finding must either change `ROADMAP.md`, `THINKING.md`, a `phase-N.md` file, mandatory commands, acceptance criteria, or be marked `checked-holds` with an evidence tier. Do not print decorative concerns.

**Output:** record this block in `THINKING.md` and surface a compact version in the Stage 6 summary:

```text
RPD_PLAN_REVIEW
Pattern Hunter: <finding + evidence tier + mutation|checked-holds>
Gonzo: <assumption + true|false|unverified + evidence tier + mutation|checked-holds>
Devil's Advocate: <failure mode + evidence tier + mitigation mutation|checked-holds>
Integrator: <touchpoints + canonical truth + split-brain risk + mutation|checked-holds>
Senior Gate: <required|skipped with reason; P0/P1/P2/P3 findings or checked-holds>
Overengineering budget: <new layers/fallbacks/agents/shims checked + mutations|checked-holds>
Mutations applied: <list or none — checked-holds>
Verdict: ready-for-review | revised-and-ready | blocked-needs-user-input
```


If the verdict is `blocked-needs-user-input`, the plan is **not approval-ready**. Do not offer "Start now" and do not proceed to Stage 7. Ask the blocking question or ask the user to explicitly accept the material assumption, then rerun the affected research/RPD checks and re-show the summary.

If files changed, re-run `validate-phase.sh` on every touched phase spec before showing the plan. Surface the post-RPD version, not the pre-RPD draft. If the review changes execution-critical context (phase criteria, RPD report path, approval gates, old-artifact rules, or launch body wording), regenerate and re-send `LAUNCH_GOAL.md`; do not make Chip ask “give me the new supergoal” after approving/reviewing the mutated plan.

Honesty check: if `RPD_PLAN_REVIEW` never mutates real weak plans and never marks `checked-holds` with evidence, it is theater and must be tightened.

### Stage 6b — Summary print

Print a scannable summary in this exact shape:

```
✓ Plan ready for review. <N> phases.

Decision:
  - <selected direction>
Why this path:
  - <why this beats alternatives>
Non-goals:
  - <what is explicitly out of scope>
Build-vs-buy:
  - <buy|wrap|fork|copy_pattern|build_fresh|defer + rationale, or skipped with reason>
Research evidence:
  - <skill perplex / docs / GitHub / source evidence, or skipped with reason>
Architect+ lite:
  - <source-of-truth / permission / failure-mode / verification summary, or skipped with reason>

Applied from memory:
  - <memory hit 1>
  - <memory hit 2>
  (or: "none — clean run")

Phases:
  1. <name> — <one-line deliverable>
  2. <name> — <one-line deliverable>
  ...
  N. Polish & Harden — every aspect verified

Stack: <stack> · pkg: <pm> · build/test/lint: <commands>

Key assumptions (correct any that are wrong):
  - <assumption 1>
  - <assumption 2>
  - <assumption 3>

Top risks & mitigations:
  1. <risk> → <mitigation>
  2. <risk> → <mitigation>
  3. <risk> → <mitigation>

RPD_PLAN_REVIEW:
  - <finding/mutation 1, or "checked-holds">
  - <finding 2 (optional)>
  - <finding 3 (optional)>
  (mutations applied in-place if any were flagged)

Artifacts:
  Roadmap: .supergoal/ROADMAP.md
  Progress: .supergoal/STATE.md (auto-updates)
  Phase specs: .supergoal/phases/phase-1..N.md

Once you confirm, I'll print a ready-to-paste `/goal` line. Paste it
once and the chain runs through to completion, with auto-retry and
fix-spec recovery.
```

Then prepare the **Telegram human review pack** before asking the start question, with a hard chat-boundary guard:

- Native files, exactly three by default: `THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`.
- They must be sent as real Telegram/file attachments before the start question. If file delivery fails, do not ask for start and do not dispatch; write/print `SUPERGOAL_REVIEW_FILES_BLOCKED` with the failed target and local paths.
- For every generated SuperGoal, add or run a scripted delivery gate such as `.supergoal/scripts/send-review-md-files.sh` and require `.supergoal/out/review-md-files-delivery-receipt.json` with `ok=true` and `sent=true`. This prevents the three `.md` files from depending on agent memory or mood.
- `LAUNCH_GOAL.md` is the only human-facing file that contains `SUPERGOAL_GOAL_BODY:` and the only document the user/operator should reply `/goal` to.
- `STATE.md`, `PROTOCOL.md`, `phases/`, reports, `context.md`, `repo-map.md`, and `tools.md` stay as internal disk/source-of-truth artifacts. Do not send them unless Chip explicitly asks for debug internals.
- **Never send SuperGoal files, launch cards, or `MEDIA:` review attachments into the post-production Telegram chat (`<post-production-chat-id>`) / the dedicated `chiptg` post-production chat.** That chat is for Telegram post previews via `tg`/the delivery account, not engineering work packages. If `/chip-supergoal` is invoked there by accident, keep artifacts local, return a compact blocker (`SuperGoal artifacts are blocked in the post-production Telegram chat; use an ops/DM chat or ask explicitly for local paths`), and do not attach files.
- Only attach the three native files in an explicit engineering/SuperGoal working chat, operator DM, or another target that Chip clearly names for SuperGoal review. If the target is ambiguous, keep files local and ask for the target rather than sending.
- If the platform supports native files and the target passes the guard, include those three as `MEDIA:<absolute-path>` attachments. If not, paste compact excerpts and clearly list the file paths.

Then send a short **launch-card** prompt via `AskUserQuestion` with header "Start chain?". The visible prompt must include the exact goal body as a `SUPERGOAL_GOAL_BODY: ...` section before the choices. Keep the card short: one line saying the three SuperGoal files are above, one `SUPERGOAL_GOAL_BODY`, then the choices.

Use these four concrete choices:

- **▶ Start Goal** — immediately run pre-flight smoke check (Stage 6.5) and then start through the official `/goal`/`GoalManager` path. On Telegram/button clients, choosing this first option is explicit start confirmation; do not ask again. Telegram recognizes choice 1 on prompts containing `SUPERGOAL_GOAL_BODY:` and starts GoalManager directly, including after clarify timeout/restart fallback. If native auto-dispatch is unavailable, fall back to the reply-shortcut launch-card (`SUPERGOAL_GOAL_BODY` + instruction to reply `/goal`).
- **✎ Assumption** — pick one assumption to change (will re-show plan)
- **⚙ Phase tweak** — change criteria, scope, or commands for a specific phase
- **↔ Restructure** — merge, split, add, or remove phases

Fallback launch rules: the user/operator can reply `/goal` to the launch-card; if the gateway supports replied document hydration, replying `/goal` to `LAUNCH_GOAL.md` is the secondary document fallback. Do not instruct Chip to reply `/goal` to `ROADMAP.md`, `THINKING.md`, `STATE.md`, `PROTOCOL.md`, or phase files. The primary UX is: exactly three files to inspect, launch-card/button or `LAUNCH_GOAL.md` reply to start. For the verified Telegram `.md` launch pipeline invariants, use `references/telegram-md-launch-pipeline-hardening.md`.

Keep options at 4 max. If the user picks any revision option, follow up with a second `AskUserQuestion` to pin down exactly what (e.g., "Which assumption?" with the assumptions listed). Apply the change, update ROADMAP/THINKING/STATE and the affected phase specs, re-run `validate-phase.sh` on each touched spec, then re-show the Stage 6 summary and launch-card again. Loop until `▶ Start Goal` or user aborts.

**Wait for the answer.** Do not dispatch `/goal` until the user picks `▶ Start Goal` (or the pre-flight bypass start option after a red pre-flight). Never assume confirmation; never start the chain on silence.

---

## Stage 6.5 — Pre-flight smoke check

After Stage 6 returns `▶ Start Goal` and **before** the official `/goal` start is allowed to proceed, run a single pre-flight pass against the deduplicated mandatory commands. This catches the case where the baseline is already broken (e.g., `pnpm build` red before phase 1 ever ran) — without this, the 3-strike loop would thrash trying to "fix" phase 1 work that was never the cause.

**Procedure:**

1. Read every `phase-N.md` spec and union their `Mandatory commands:` lines into a deduplicated set.
2. Run only commands that are clearly safe pre-flight checks (build/test/lint/typecheck/static smoke). Do not run deploy, migration, write-heavy, network-mutating, payment, credential, destructive, or production commands in `/chip-supergoal`; move those to the future `/goal` phase or require explicit user approval. Capture exit code and last ~5 lines for every command that is run, and list skipped side-effectful commands separately.
3. **If all green:**
   - Append a `Notable events` line to `.supergoal/STATE.md`: `<DATE> — Pre-flight green: <N> commands clean.`
   - Print `PREFLIGHT_GREEN` with the per-command summary.
   - Proceed to Stage 7.
4. **If any red:**
   - Append `<DATE> — Pre-flight red: <cmd> exited <code>.` to `STATE.md`.
   - Print `PREFLIGHT_RED` with the failing command, exit code, last ~5 lines.
   - Re-show the Stage 6 summary with the failures surfaced and a revised menu (still 4 options to stay under the `AskUserQuestion` ceiling): **"Skip pre-flight, dispatch anyway"** (replaces "Start now" — the user might know the baseline is intentionally broken, e.g., phase 1's whole job is to fix it) / **"Adjust an assumption"** / **"Tweak a phase"** / **"Restructure phases"**. If "Skip pre-flight, dispatch anyway" → log `<DATE> — Pre-flight bypassed by user.` and proceed to Stage 7. Any other choice loops back through the normal Stage 6 revision flow; after the user finishes revising, Stage 6.5 re-runs.

**Honesty test:** real command run, real exit code. The "skip anyway" option keeps the user in control — no forced re-plan if the baseline being red is the point.

### Baseline-red triage pitfall

When the user asks to add a baseline-fix phase after `PREFLIGHT_RED`, do not patch only the first visible failing file and call the baseline fixed. Re-run the failing command and classify every remaining failure before dispatch:

- `phase-created-missing` — files expected to be created by later phases; OK to keep red until that phase.
- `unrelated-existing-baseline` — repo-wide historical failures outside the requested surface; record explicitly in `STATE.md` and avoid making them acceptance gates unless the user expands scope.
- `owned-baseline-regression` — failures inside the requested surface or required verifier; add/adjust Phase 0 until the verifier is green or the user explicitly approves bypass.

If direct `tsc --noEmit` is red but the project’s CI-equivalent `npm run build` is green, prefer the repo’s real CI/build gate for completion claims and state the residual direct-TS baseline separately.

When pre-flight is red on an owned baseline problem inside the requested surface, add an explicit baseline-repair phase before dispatch instead of burying the failure in Phase 1. Practical pattern: insert `Phase 0 — Baseline test repair`, shift existing phases forward, validate every `phase-N.md`, rerun the failing minimal command, then ask the user whether to bypass pre-flight because Phase 0 now owns the red baseline. Record the bypass in `STATE.md`. Do not claim pre-flight green unless the command actually passed.

---

## Stage 7 — Launch through `/goal`

Slash commands on both Claude Code and Codex fire **only from user input**. Telegram launch buttons are a special gateway path: the first launch-card button can call the official GoalManager directly, but only because Stage 6 already got explicit human confirmation. If button start is unavailable, use the reply fallback on the launch-card. After explicit `▶ Start Goal` in Stage 6:

**Dispatch path discipline:** default to repo-relative `.supergoal/...` paths and tell the user to start `/goal` from the project root. Do not print absolute local paths by default because they can leak workstation or account details. If the current client cannot run from the project root and absolute paths are genuinely required, ask for explicit confirmation before emitting them, then verify the target root exists and every phase spec validates.

**One confirmation means one confirmation:** if pre-flight is red and the user explicitly chooses "Skip pre-flight, dispatch anyway", do not ask the same question again. Log the bypass in `STATE.md`, prepare Stage 7 artifacts, and print the ready-to-paste `/goal` line immediately.

1. Update `STATE.md`: `Status: READY_TO_DISPATCH`, `Current phase: <first phase number>`, and **capture the baseline ref** — set `Baseline ref:` to the output of `git rev-parse HEAD 2>/dev/null || echo "no-git"`. Use whatever numbering the phase files actually use (`phase-0.md` starts at 0; `phase-1.md` starts at 1). Do not hard-code `Current phase: 1` when the plan is zero-indexed. The audit reads the baseline to diff deliverables against the working tree.
2. Copy `$SUPERGOAL_DIR/templates/PROTOCOL.md` to `.supergoal/PROTOCOL.md` (the operating manual the executing agent reads at the start of the `/goal` session), and copy `$SUPERGOAL_DIR/scripts/repo-state.sh` to `.supergoal/repo-state.sh` (the complete-working-tree comparison helper the cleanliness + deliverable checks invoke; strategy in `references/repo-state-comparison.md`).
3. Verify each `.supergoal/phases/phase-N.md` exists; run `bash $SUPERGOAL_DIR/scripts/validate-phase.sh .supergoal/phases/phase-<N>.md` on each.
4. **Client-safe launch UX:** do not put the `/goal` command in a fenced code block. Prefer this shape. For Telegram `.md` launch details and the `LAUNCH_GOAL.md` tail-extraction pitfall, see `references/telegram-md-goal-launch-ux.md`.

   a. Send/re-show the short launch-card containing `SUPERGOAL_GOAL_BODY:` and the four choices `▶ Start Goal`, `✎ Assumption`, `⚙ Phase tweak`, `↔ Restructure`.

   b. On Telegram/button clients, the first button starts GoalManager directly against the visible `group:<chat_id>:<thread_id>` session and queues the raw goal body (not a synthetic `/goal ...` message).

   c. Fallback: if the button path is unavailable or fails, tell the user/operator to reply to the launch-card with exactly `/goal`. Do not make him copy the long text.

   d. Secondary fallback: if the gateway supports replied-document hydration, Chip may reply `/goal` only to `LAUNCH_GOAL.md`, not to the other review/internal files. Send only three human-facing files by default: `THINKING.md`, `ROADMAP.md`, and `LAUNCH_GOAL.md`. `LAUNCH_GOAL.md` may include `DONE_CONDITION:`, `OPERATOR_ACTION:`, and `NOTES:` sections after `SUPERGOAL_GOAL_BODY:`; the gateway must strip those tails and store only the raw goal body, never the whole file wrapper. The Telegram adapter must keep bare `event.text == "/goal"` and put the file body into `event.reply_to_text`; otherwise `_handle_goal_command()` sees the whole file as args and stores garbage. See `references/telegram-md-goal-launch-hardening.md` and `references/telegram-md-launch-pipeline-hardening.md`.

   e. Also include a fallback plain-text one-liner for non-reply clients, outside code fences and labeled `Fallback plain-text line:`. The line must begin with `/goal "..."` and use repo-relative paths unless the user explicitly approved absolute paths.

5. If the user sends only the quoted body without `/goal`, treat it as a dispatch UX failure. Do not scold or make them copy the same long body again. Re-send the short reply method only: `Reply to the SUPERGOAL_GOAL_BODY message with /goal`.

6. If the user later pastes only the quoted condition body without the leading `/goal`, treat that as a failed dispatch UX, not as an automatic command. Reply briefly that the leading slash is required and re-print the one-line command unless the current platform has already started a goal/continuation wrapper.

7. **Copy formatting pitfall:** chat clients can copy the wrong region or omit the leading slash from long copied text. The primary mitigation is the reply shortcut. Only use the long plain-text `/goal "..."` fallback when reply mode is impossible. If a goal-wrapper is already active despite the formatting, continue executing instead of looping on paste instructions.

8. **Standing-goal continuation pitfall:** If the next user message arrives as a host-generated wrapper such as `[Continuing toward your standing goal]` or explicitly says `Continue working toward this goal`, the `/goal` wrapper is already active. Do not re-print paste instructions or ask for `/goal` again. Read `STATE.md`, continue from the current phase or `AUDIT`, run only the next numbered phase in this turn, and emit the required `SUPERGOAL_PHASE_VERIFY`, `SUPERGOAL_PHASE_DONE`, `SUPERGOAL_TURN_YIELD`, `AUDIT_COMPLETE`, and `SUPERGOAL_RUN_COMPLETE` markers in the visible transcript when earned.

9. **Stop.** Do not generate any further output. The chip-supergoal invocation ends here. The user's paste begins the autonomous run under a fresh `/goal` session, which reads `PROTOCOL.md`, `ROADMAP.md`, `STATE.md`, and the phase specs from disk and runs the loop documented in the next sections.

Once `/goal` is active (you'll see the `◎ /goal active` indicator on Claude Code), the per-turn evaluator keeps the agent working until the end-state condition holds. On Codex, the auto-continuation loop does the same. The agent inside the `/goal` session has zero special context from the chip-supergoal invocation; everything it needs is in the files on disk — by design.

---

## Phase execution loop (inside the single `/goal` session)

The agent's loop is spread across `/goal` continuations until `SUPERGOAL_RUN_COMPLETE`. **Run at most one numbered phase per assistant turn.** This is non-negotiable: it prevents long runs from hitting the per-turn tool-call/context limit before deployment or final audit.

1. Read `STATE.md` → find current phase N. If `Current phase: AUDIT`, skip to Final audit.
2. Read `.supergoal/phases/phase-N.md` → full work spec.
3. Print `SUPERGOAL_PHASE_START` block with values from the spec.
4. Print a compact `SUPERGOAL_STATUS` block for the human operator before work begins. Use `references/supergoal-status-snapshots.md`: Project Flow-style readability, but no Project Flow rail/supervisor/lane runtime. This is additive; never remove the formal evaluator markers.
5. Do the work; run mandatory commands; surface evidence into the transcript.
6. Print `SUPERGOAL_PHASE_VERIFY` block (every criterion `pass|fail` + engineering checks + **cleanliness checks** — grep `bash .supergoal/repo-state.sh added-lines <Baseline ref>` (complete added/new lines since baseline, **including uncommitted and untracked work**) for stack-specific debug prints, session TODO/FIXME, dead imports; non-zero counts trigger 3-strike unless the phase spec declares `Cleanliness override:`). Include an updated `SUPERGOAL_STATUS` snapshot showing check state and latest evidence.
   - **Acceptance-map hardening:** before `SUPERGOAL_PHASE_DONE`, map every acceptance criterion to an explicit test/probe/evidence line. If a criterion is only implied by a green broader test, add a focused test/probe now and rerun the mandatory verifier. Do not close risky/payment phases on “covered by design” unless the evidence proves the negative path (for example duplicate webhook, wrong order id, amount mismatch, or redirect-not-authoritative behavior).
7. **RPD phase review** — if the phase spec declares `RPD required: yes` or touches a risky area, run `RPD_PHASE_REVIEW` from `.supergoal/PROTOCOL.md`. Findings must mutate the work/spec or be marked `checked-holds` with evidence.
8. **Memory writeback check** — anything non-obvious learned? If yes, write a memory file under the detected MEM_DIR; print `MEMORY_SAVED: <name>` (or `MEMORY_SAVED: none`).
9. Print `SUPERGOAL_PHASE_DONE`, update `STATE.md` (mark phase N complete; if N < total, set Current phase = N+1; if N == total, set Current phase = `AUDIT`; append events line). Update the live status snapshot fields in `STATE.md`.
10. Print `SUPERGOAL_TURN_YIELD` with the next state and a final `SUPERGOAL_STATUS` snapshot, then stop this assistant turn. Do **not** start phase N+1 in the same turn. The `/goal` judge will auto-continue because `SUPERGOAL_RUN_COMPLETE` is still missing.
11. On the next standing-goal continuation, repeat from step 1.
12. If `Current phase: AUDIT`: run the **Final audit** (next section). Only after `AUDIT_COMPLETE`, print `SUPERGOAL_RUN_COMPLETE` with a 5-line summary. Then add a short plain human-language summary for Chip: what was done, what was verified, and what was not touched / remains gated. This must be readable without understanding SuperGoal markers. The `/goal` condition is now satisfied and clears.

### Final audit (Stage 10 of the loop — before completion)

Per-phase VERIFY blocks are self-reports. A phase can pass its own check while a later phase silently breaks it (a type added in phase 2 violated in phase 5; tests that passed mid-run break after refactor; etc.). The audit closes that loophole by re-validating against the **original** ROADMAP.md, not against the run's own self-reports.

The audit runs once after the final phase. If it finds gaps, it writes a focused fix spec and re-runs itself. Cap at 3 audit rounds; on the 3rd round's failure, `AUDIT_HANDOFF`.

**Audit steps:**

1. Print `AUDIT_START` with round number, total phase count, criteria count, and the deduplicated set of mandatory commands to re-run.
2. **Re-read `ROADMAP.md`** — pull every phase's acceptance criteria fresh from the original plan. Do not trust prior VERIFY summaries.
3. **Phase completeness check** — scan the transcript: does every phase 1..N have a `SUPERGOAL_PHASE_DONE` block? Surface any missing.
4. **Re-run aggregated mandatory commands** once each (build, typecheck, lint, full test suite). Surface last ~10 lines + exit code for each. Any non-zero exit → an `AUDIT_GAP`.
5. **Spot-check verifiable criteria** — for each acceptance criterion across all phases:
   - "File X exists" / "Function Y exported" / "Config key Z set" / "No `console.log` in app code" → re-check via `ls`/`grep`/`cat`.
   - "Screenshot showed X" / "Manual smoke test passed" / other non-deterministic checks → mark `trust-prior-verify`, do not re-run.
5b. **Deliverable check** — for each phase block in `ROADMAP.md`, parse the `**Deliverables:**` bullets. For each bullet that names a file path or glob, run `bash .supergoal/repo-state.sh deliverable <Baseline ref> "<path>"` — it checks the **complete working tree** (committed + staged + unstaged + deleted) against the baseline and detects untracked new files separately. `missing`/`deleted` (exit 1), invalid baseline (exit 2), or unchanged pre-existing (exit 3) → `AUDIT_GAP: phase <N> deliverable "<bullet>" not proven as delivered by this run`, unless the roadmap explicitly marks that deliverable as pre-existing / verification-only. Repository ground-truth — catches "agent said done but didn't ship," even when the run never committed. Strategy: `references/repo-state-comparison.md`.
   - **`.supergoal/` report exception:** `.supergoal/` artifacts are often untracked, pre-created, or rewritten outside the git baseline; `repo-state.sh deliverable` can legitimately return `unchanged — existed before baseline` for reports/state/protocol even when the current file is the correct audit artifact. For `.supergoal/reports/*.md`, `STATE.md`, `ROADMAP.md`, and `PROTOCOL.md`, verify `test -s`, line counts, required markers, and current timestamps/content instead of treating baseline-unchanged as an audit gap. Use repo-state deliverable for product files, scripts, tests, docs, and other source deliverables.
6. Print `AUDIT_VERIFY` block:
   - Per-phase status (DONE present or missing)
   - Each mandatory command's exit code
   - Each criterion's `pass | fail | trust-prior-verify` with evidence
   - `Deliverables:` block from step 5b — `phase N / "<bullet>": present | missing`
7. **If any gaps:**
   - Print `AUDIT_GAPS` with the list.
   - Write `.supergoal/phases/audit-fix-<round>.md` — a focused fix spec targeting **only** the failing criteria, with the original phase's VERIFY as the success gate, scope creep forbidden.
   - Execute the fix spec inline (same agent, same `/goal`, same 3-strike per-criterion protocol from regular phases).
   - On fix success: loop back to step 1 (round + 1). On 3rd round's failure: print `AUDIT_HANDOFF` (full gap history + suggested next move), update `STATE.md` to `BLOCKED`, stop. Do not print `SUPERGOAL_RUN_COMPLETE`.
8. **If clean:**
   - Compute `audit coverage` = `re_verified / (re_verified + trust_prior)` as a percentage (where `re_verified` = criteria with `pass` + deliverables marked `present`; `trust_prior` = criteria marked `trust-prior-verify`).
   - Print `AUDIT_COMPLETE` with phases verified, commands re-run clean, criteria pass/trust-prior counts, deliverables present/missing counts, and the audit coverage %.
   - Print `SUPERGOAL_RUN_COMPLETE`. If `trust_prior / (re_verified + trust_prior)` > 30%, prepend an honesty banner: `⚠ Audit coverage: X re-verified, Y trust-prior (Z%). Eyeball UI/UX before merging.` Below 30%, print the plain coverage line without the warning prefix.

The audit is the difference between "every phase passed its own self-report" and "the final state matches the plan I originally approved." That is the bar.

### Failure recovery (3-strike, built into the protocol)

**First failure of any criterion:**
1. Print `FAILURE_PROBE` (what failed, what tried, root-cause hypothesis).
2. Append probe to `STATE.md` failure log.
3. **Auto-retry the same phase once** with the probe injected as feedback. Do not advance.

**Second failure (auto-retry also failed):**
1. Print `FAILURE_ESCALATE`.
2. Write a focused **fix spec** at `.supergoal/phases/phase-N.fix.md` (targets only the failing criterion, no scope creep).
3. Execute the fix spec inline (same agent, same `/goal` — no new dispatch). On success, re-run the original phase's VERIFY block; on pass, advance to N+1.

**Third failure (fix spec also failed):**
1. Print `FAILURE_HANDOFF` with: failing criterion, full probe history, three things tried, suggested next move.
2. Update `STATE.md`: `Status: BLOCKED`. The user takes the wheel.
3. The `/goal` condition will not be satisfied; the host's evaluator will keep evaluating but the agent should stop attempting and surface the handoff clearly.

This recovers from flaky envs, simple typos, and missed deps automatically. Only real blockers escalate.

### Mid-run interruption

If the user sends any message during the `/goal` run, the agent pauses at the next phase boundary, addresses the message, and asks before resuming. Phase boundaries are after `SUPERGOAL_PHASE_DONE` and before reading the next phase spec.

**Telegram frustration / "why did you stop" pitfall:** the formal "one numbered phase per turn" rule is for evaluator/context safety, but in Telegram it can feel like pointless stalling. If Chip reacts with frustration such as "почему остановился", "продолжай", "не тормози", "где goal", "почему работа не идёт", a bare `?`, or otherwise clearly wants the next concrete step, treat that as explicit permission to resume immediately. Do not only defend the protocol or explain GoalManager mechanics. Briefly acknowledge the friction in one line, then read `STATE.md`, run the next current phase, write real evidence/report files, update `STATE.md`, and emit the normal `SUPERGOAL_PHASE_START` / `VERIFY` / `DONE` / `TURN_YIELD` markers. Keep the phase-boundary bookkeeping, but keep the visible answer compact: Chip does not need the full phase header repeated after a confusion marker unless the evaluator requires it. Do not require Chip to paste `/goal` or a new launch instruction again. If the official GoalManager auto-continuation is visibly stalled in Telegram, the correct recovery is execution of the current phase from disk, not another launch-card explanation.

If the next current phase is approval-gated, a bare `?` / frustration nudge authorizes only the safe read-only part of that phase: inspect current state, write the approval-gate report, update `STATE.md` to `BLOCKED_BY_APPROVAL`, and show the exact approval phrase. It does **not** authorize the side effect itself. This avoids the bad loop where the agent either re-explains the previous phase or waits idly, while still preserving the production gate.

**User-triggered skill-library review exception:** if the user/operator asks to “review the conversation and update the skill library” during an active SuperGoal, pause the phase loop for that turn and update the relevant loaded class-level skill(s). Do not emit SuperGoal phase markers in the skill-update reply; the next standing-goal continuation resumes from `STATE.md`.

If the user says the formal SuperGoal format is too heavy or asks to leave it, do not keep producing phase ceremony. Mark `.supergoal/STATE.md` honestly as paused/deferred/deprioritized or blocked, keep useful artifacts on disk, and switch to the requested lighter execution mode with compact factual state tracking. Only resume numbered phases if the user explicitly restarts the chain.

- **Interrupted-turn / gateway-shutdown recovery pitfall**

If a SuperGoal turn is interrupted by gateway restart, context compression, or tool-call shutdown while a phase is half-done, do not answer defensively or restart the phase from scratch. Resume actively. If the prior turn already received explicit `▶ Start Goal`/start confirmation but was interrupted before a clean visible kickoff proof, and the next message arrives as `[Continuing toward your standing goal]` or the user/operator asks “Где goal?”, treat the goal wrapper as active enough to continue from disk: finish processing the previous tool results, update/report the current phase if earned, and emit the normal phase markers. Do not reopen the launch-card or ask for another start.

1. Re-read `STATE.md` and the current `phase-N.md`.
2. Inspect any partial evidence files already written under `.supergoal/evidence/phase-N/`.
3. Finish missing edits, mandatory commands, reports, `STATE.md` update, and visible transcript markers.
4. If the user asks “ты бросил?” / “обнови supergoal” / “продолжай”, treat it as a request to complete the current phase bookkeeping and advance the state if acceptance is satisfied — not as a new planning task.
5. When an exact mandatory verifier fails with a known baseline-red class captured in Phase 0, preserve the exact failing output, run a narrower supplemental check only to classify whether the phase introduced a new failure, and write that classification into the phase report. Do not hide the red verifier, but do not block a docs/contracts phase on a pre-existing timeout or unrelated executable-bit drift.
6. Before printing `SUPERGOAL_PHASE_DONE`, verify the report exists, `STATE.md` advanced, and evidence files are present on disk. The answer should say what was actually completed, not promise to do it next.

- **Git cleanup during active SuperGoal pitfall:** if the user/operator asks to clean local git / fast-forward prod while a SuperGoal is in progress, preserve dirty product work into a WIP branch first, then clean the main branch. Because `.supergoal/` is usually gitignored, `STATE.md` can still say phases are complete after tracked deliverables vanished from the active tree. Before resuming, verify completed-phase deliverables exist in the current worktree; if not, rehydrate only the phase allowlisted files from the WIP branch and rerun focused verifiers. See `references/supergoal-git-cleanup-resume.md`.

### Tool/context limit checkpoint pitfall

See also `references/supergoal-goal-pipeline-turn-yield.md` for the Supergoal→`/goal` pipeline lesson and the exact turn-yield marker contract.

For production deploy phases, also use `references/production-deploy-gates.md` before writing or executing the phase spec. It captures the submodule/gitlink, untracked product file, canonical deploy-ref, runtime symmetry, and live-smoke gates that prevent “tests passed locally but production ran old code.”

When modifying and publishing the `chip-supergoal` skill package itself, use `references/skill-package-sync.md`: focused file copy, validation, secret scan, private repo + powerpack nested sync, and public-repo sanitation guard.

If the user reports a live production gap after deploy, or before final audit on a production SuperGoal, use `references/post-deploy-repair-audit.md`: verify public route, release SHA, runtime env symmetry, untracked/excluded files, add a repair addendum, then rerun final audit before `AUDIT_COMPLETE`. Do not complete from pre-repair evidence.

For auth/OAuth SuperGoals, browser-visible login buttons are not proof that provider login works. Final audit must probe the provider backend layer too: `/auth/v1/settings` with the public anon/publishable key for built-in providers, and `/auth/v1/authorize?provider=<id>&redirect_to=<callback>` for exact custom provider IDs. If Supabase/GoTrue says `provider is not enabled` or `Provider <id> could not be found`, hide the button or block honestly instead of claiming OAuth is ready. For the detailed probe/checklist pattern and Human20 Yandex bridge example, use `references/auth-oauth-provider-audit.md`.

For auth UI polish phases, also use `references/auth-ux-polish-phase.md` before verifying the phase. It captures the expanded surface rule (login/reset/activation/Telegram/promo), safe query-error copy, one-CTA-plus-recovery matrix, theme/heading guardrails, and RPD UX prompts.

Long production Supergoal runs can hit tool-call or context limits after verification has succeeded but before the agent writes the phase report, updates `STATE.md`, or emits `SUPERGOAL_PHASE_DONE`. Prevent orphaned verified work:

- After any expensive green verification block, immediately write the phase report and update `STATE.md` before starting additional review, cleanup, deploy, or next-phase work.
- Do not persist `bash .supergoal/repo-state.sh added-lines <baseline>` output into an untracked path under `.supergoal/` while that output is being generated. The repo-state helper can include untracked files, so writing the output inside the untracked tree can self-include and grow unbounded. Write large added-lines captures to a temp path outside the repo, or run a bounded targeted scan instead.
- After `SUPERGOAL_PHASE_DONE`, print `SUPERGOAL_TURN_YIELD` and stop the turn. Never chain into the next numbered phase in the same assistant turn; `/goal` continuation is the pipeline.
- If interrupted by context compression or tool-call limit, do not claim the phase is formally complete unless `STATE.md` and the visible transcript contain the required `SUPERGOAL_PHASE_DONE` marker.
- On resume, trust disk state over prior self-report: read `STATE.md`, inspect reports/logs already written, then either finish the missing bookkeeping for the current phase, continue executing that phase, or run `AUDIT` if `STATE.md` says so. Do not skip to deploy/final audit based only on remembered green commands.
- If `STATE.md` has already advanced to phase N+1 but the visible transcript is missing the required markers for phase N, perform a bookkeeping-only turn: print the missing `SUPERGOAL_PHASE_VERIFY`, `MEMORY_SAVED`, `SUPERGOAL_PHASE_DONE`, and `SUPERGOAL_TURN_YIELD` from real report/log evidence, then stop. Do not redo phase N and do not start phase N+1 in the same turn. See `references/phase-marker-bookkeeping.md`.
- Prefer saving command logs under `.supergoal/<goal>/logs/` during phase 6+ verification so a resume can cite real evidence without rerunning every expensive command.
- When delegating `RPD_PHASE_REVIEW` or `RPD_FINAL_REVIEW`, pass the exact SuperGoal root path (normally `.supergoal/`), the absolute repo path, and the expected `STATE.md` path. Do not let the reviewer search for a root-level `STATE.md`; a false “STATE missing” caveat wastes audit confidence. Treat subagent review as advisory and verify its caveats yourself before printing `AUDIT_COMPLETE`.

---

## Memory writeback rules (referenced by PROTOCOL.md)

Memory is load-bearing. Future runs start smarter because past runs wrote down what they learned. The phase execution loop's step 6 references these rules.

**At each phase boundary**, ask: "Did this phase surface anything a future chip-supergoal run on a similar task would benefit from knowing?"

Worth saving:
- A library API quirk that wasn't in the docs
- A user preference confirmed during this run ("user accepted dark-only UI without pushback")
- A project-level fact ("auth lives in `lib/auth/` not `app/api/auth/`")
- A failure pattern + fix ("X always fails on first build; second build works")

Write the memory file under the detected MEM_DIR using the standard `name` / `description` / `metadata.type` frontmatter. Link it from `MEMORY.md`. Print `MEMORY_SAVED: <name>` to the transcript. If nothing non-obvious this phase: print `MEMORY_SAVED: none`.

**At the final phase**, always write a `project_<slug>.md` memory pointing at the new/changed project (location, stack, status, ROADMAP link). Guarantees future chip-supergoal runs on the same project start from the latest state.

**Never save:** secrets, transient task details, ephemeral state. Bar is "useful to a future run." When in doubt, skip.

---

## Operating principles (read every run)

- **One `/goal`, short condition, many turn continuations.** `/goal` takes an end-state, not a task body. Long content lives in files the agent reads from disk. The executor runs one numbered phase per assistant turn, yields, and lets `/goal` continue. This is the natural shape on both Claude Code and Codex.
- **Approval-blocked continuations stay blocked.** If `STATE.md` is already `BLOCKED_BY_APPROVAL`, a later `/goal`/“Continue working toward this goal” message is not approval when the goal text still requires explicit approval for prod/bot side effects. Confirm the blocker with minimal state inspection, do not rerun preflight/tests just because the host loop fired, and answer compactly: `BLOCKED_BY_APPROVAL`, current phase, exact missing approval target/action, and the shortest acceptable approval phrase or gate-report quote. Do not advance, print `SUPERGOAL_PHASE_DONE`, restart, smoke, spend, or soften the gate on repeated non-approval continuations.
- **Frictionless is the goal.** Memory + prompt + recon should answer most questions. Zero clarifying questions on well-described tasks is a win.
- **Adapt to available tools.** Detect what's there (Context7, WebSearch, MCPs, skills). Use what's available; degrade gracefully without it. Never hard-require a tool that might not be present.
- **Memory is load-bearing.** Preload at Stage 0, surface as "Applied from memory: …" in Stage 1, write back at every phase boundary.
- **"Perfect" is not a stopping condition — criteria are.** Translate every "perfect" into observable, falsifiable criteria.
- **Two human gates, no more.** Clarifying gaps (Stage 1 — walk the full category checklist for greenfield in batches of up to 4 until all material info is gathered; often zero for brownfield) and plan review (Stage 6). Between and after, autonomous.
- **The loop self-heals.** Auto-retry once, then write a fix spec and execute inline, then escalate. Don't stop on first failure.
- **The evaluator only sees the transcript.** Phase specs require the agent to surface their contract — START, commands, evidence, VERIFY, DONE — into the conversation, not just point at files.
- **Each phase is independently shippable** in spirit. If phase 3 can't build/test on its own, the slicing is wrong.
- **The Polish & Harden phase is mandatory.** It's how "every aspect is perfect" gets enforced.

---

### Hermes compatibility notes

- **Skill directory detection:** if the standard Claude/Codex snippet cannot find the skill directory, include the Hermes-native path too: `$HOME/.hermes/skills/chip-supergoal/SKILL.md`. The skill is multi-file; a single raw `SKILL.md` install is not enough.
- **Telegram plan review attachments:** Stage 6 may attach the SuperGoal review pack only in an explicit engineering/SuperGoal working chat. Never attach SuperGoal `.md` files, launch cards, or `MEDIA:` review artifacts into the post-production Telegram chat (`<post-production-chat-id>`) / the `chiptg` post-production chat; that surface is reserved for Telegram post previews via `tg`/the delivery account. In the post-production Telegram chat, keep artifacts local and return a compact blocker or ask for an ops/DM target. In allowed chats, do not rely on the clarify body as the only visible artifact: if `clarify` times out or cannot carry media, final-reply with the native files or artifact paths plus a short “not started” status. Never imply the Supergoal started unless `▶ Start Goal` was explicitly chosen or visible `/goal` state is verified. If the user/operator says “я не вижу файлов” / “where are the files” in an allowed SuperGoal chat, treat it as a workflow miss: immediately send the native human review files (`THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`) as `MEDIA:` attachments, then give the short launch instruction; do not re-explain the plan or ask him to hunt local paths.
- **Files-first launch-card sequencing:** When the user/operator asks for a smoother SuperGoal process, do not send a long inline plan as the main artifact. Generate native review files first, validate phase specs, then present one short `SUPERGOAL_GOAL_BODY:` launch-card with exactly four choices. If the `clarify`/button response immediately returns `▶ Start Goal`, run the safe pre-flight and live GoalManager verification before the final reply, then include the native file attachments/status in that final reply so Chip still receives the review pack. This preserves the desired UX: files for review/source-of-truth, button for start, reply `/goal` to card or `.md` only as fallback.
- **Clarify is not file delivery:** Creating SuperGoal review files on disk is not enough in allowed SuperGoal chats. Before or alongside the launch-card, send the three human-facing files as native `MEDIA:` attachments (`THINKING.md`, `ROADMAP.md`, `LAUNCH_GOAL.md`) in a visible final/chat message, **but never in the post-production Telegram chat / `chiptg`**. If `clarify` times out, is interrupted, or returns without media support, follow up with the attachments only when the target passes the chat-boundary guard; otherwise keep files local and ask for an ops/DM target. Do not make Chip ask “I don’t see files” before sending them in an allowed SuperGoal chat. If the operator explicitly says “attach in this chat” or corrects a false `chiptg` classification, treat the current thread as the intended engineering/SuperGoal target unless it is the exact blocked post-production chat chat; immediately attach the three native files, and if he asked for all artifacts attach a full `.supergoal/` zip too. See `references/supergoal-review-artifact-delivery.md`.
- **Do not overapply the post-production chat guard:** If Chip corrects that the current thread is the intended Dev/engineering/SuperGoal chat, treat that correction as explicit target authorization for this chat. Immediately attach the three human-facing files here. If the user/operator asks for “all artifacts” or “especially supergoal”, also attach a single zip/bundle of the full `.supergoal/` tree with secrets/runtime caches excluded. Do not argue about `chiptg`; the guard is only for the dedicated post-production preview chat, not every thread that happens to mention Telegram.
- **Do not over-apply the `chiptg` guard:** The the post-production Telegram chat block is narrow. Do not infer a blocked post-production chat merely from a group/reply transcript, Telegram context, or the fact that SuperGoal artifacts are being discussed. If the user/operator says this/current chat is where artifacts belong, or corrects “attach in this chat,” treat that as target clarification: attach `THINKING.md`, `ROADMAP.md`, and `LAUNCH_GOAL.md` natively, and if he asks for all artifacts attach a full `.supergoal/` zip in addition. Do not substitute zip-only delivery for the three human-facing files. See `references/telegram-supergoal-artifact-delivery-correction.md`.
- **Monorepo/package-subdir detection:** `detect-stack.sh` at repo root can miss package managers when the app lives in a subdirectory such as `frontend-v2/`. During recon, inspect likely package roots (`package.json`, `pnpm-lock.yaml`, `package-lock.json`, `pyproject.toml`, etc.) and write phase commands with explicit `cd <package-dir> && ...`. Do not leave `Stack: unknown` or root-level commands when the relevant package metadata is present one level down.
- **Generated check scripts must be standalone:** when `/chip-supergoal` writes helper scripts under `.supergoal/checks/` for a future `/goal` run, do not import `hermes_tools` or other planner-only modules. Those scripts are normally executed by `terminal` from the project root, outside the `execute_code` sandbox. Use plain Python stdlib / shell commands, or mark the check as a Hermes-tool smoke that the executing agent must perform directly. If a generated check accidentally imports `hermes_tools`, patch the script immediately, rerun the mandatory command, and record the failed first attempt as a tooling-design bug, not an environment blocker.
- **Generated secret/static scans must not self-trigger:** if a phase creates `static-lint`, secret-scan, or unsafe-surface scan scripts, avoid embedding literal private-key banners or real secret env assignment examples inside files that the script scans. Build sensitive regexes from string parts or scan only source/test roots that exclude the checker itself. Do not weaken the scan; prevent the checker from matching its own source, then rerun the mandatory command and record the first failure as a tooling-design fix.
- **Pre-flight interpretation:** chip-supergoal pre-flight can be red because a phase deliberately creates files referenced by later mandatory commands (for example a new `rentals-admin.test.ts` that Phase 1 will create). Classify these as expected phase-created-file failures, not baseline blockers. Separate them from unrelated baseline failures such as an existing TypeScript error elsewhere.
- **Baseline red handling:** if baseline is red on unrelated files, offer the user two concrete choices: add a Phase 0 to fix the baseline, or dispatch anyway and require later phases/final audit to prove the failure is pre-existing and unrelated. Do not silently start after a red pre-flight.

- **Standing-goal finish requests:** When the user/operator asks to make a Supergoal for finishing an existing plan, do not re-plan from zero and do not manually continue the phases. Read the existing `STATE.md`, preserve completed phases, patch stale specs to include `SUPERGOAL_TURN_YIELD`, write a concise finish body artifact, and dispatch/resume from the current phase only. See `references/supergoal-goal-pipeline-turn-yield.md`.
- **Telegram launch-card UX:** Prefer files-first review plus one short launch-card with `SUPERGOAL_GOAL_BODY:` and four buttons (`▶ Start Goal`, `✎ Assumption`, `⚙ Phase tweak`, `↔ Restructure`). Treat files as review artifacts and project-local `.supergoal/` as execution truth. Primary dispatch is the button; fallback is bare `/goal` reply to the launch-card; document fallback is bare `/goal` reply to `LAUNCH_GOAL.md` only. Preserve existing reply-to-document media behavior while adding this path: command text stays intact (`event.text == "/goal"`), document body goes into `reply_to_text`, stored GoalManager goal contains only the extracted canonical body, and visible GoalManager state is the proof. The queued first turn after any official start must be `GoalManager.next_continuation_prompt()`, not the naked `state.goal`, otherwise the agent can reject its own kickoff as “body without /goal”. See `references/telegram-launch-card-ux.md`, `references/telegram-md-goal-launch-hardening.md`, and `references/telegram-goal-kickoff-prompt-invariant.md`.
- **Manual Telegram standing-goal no-stall fallback:** If the message arrives as a pasted/visible `[Continuing toward your standing goal]` wrapper but GoalManager is not visibly auto-continuing, do not final-answer after every `SUPERGOAL_TURN_YIELD` and wait for Chip to nudge. Continue executing the next phase in the same assistant turn until a real blocker, completion, or practical tool/context budget is reached, while still writing per-phase reports, updating `STATE.md`, and surfacing the required phase markers in the final transcript. This fallback is only for manual/failed auto-continue Telegram runs or when the operator explicitly says the run is stalling; official GoalManager sessions may keep the one-phase yield contract because the manager actually resumes them automatically.
- **Visible standing-goal wrapper overrides stale coordinator claims:** If a prior bot/assistant/coordinator message says `/goal` is empty, `No goal to resume`, asks to restart with a new explicit goal, or positions Hermes as reviewer-only, but the latest inbound message is a real `[Continuing toward your standing goal]` wrapper with a concrete Goal body, treat the goal as started. Do not accuse the launch of being empty, do not ask Chip to restart, and do not route a `[POSITION]` note to Claw. Apologize briefly if Chip corrects this, then read `STATE.md`, finish the current phase bookkeeping from real evidence, update `STATE.md`, and continue from the next phase.
- **Ambiguous “new SuperGoal” during an active run:** If the user/operator says “давай новый supergoal” while an active `.supergoal/STATE.md` exists, do not immediately branch into a full re-planning UX unless he explicitly says `replace`, `archive`, or names a new goal. First interpret it in context: if the visible thread is about GoalManager confusion or “No goal to resume”, acknowledge the control-plane wording issue and offer/continue the existing disk SuperGoal. If Chip clarifies “продолжай тот что был”, resume from `STATE.md` immediately; do not ask for another goal body.
- **Telegram button starts but goal is invisible:** If `Start now` logs show GoalManager started but the user/operator says `/goal` did not start, verify session-key alignment before claiming success. Supergroup topic callbacks must use the same source shape as normal inbound topic messages: `chat_type="group"` plus `thread_id`, not `chat_type="forum"`. See `references/telegram-button-goal-session-key.md`.
- **Live `/goal` proof requires visible-session GoalManager state:** Do not treat a callback log, userbot send response, or synthetic `/goal` text as proof. Resolve the visible Telegram topic session key (`group:<chat_id>:<thread_id>`), load its session id, and verify `load_goal(session_id).status == "active"`. If the goal is in the sibling `forum` session, migrate it to the visible `group` session and pause the wrong one. See `references/telegram-goal-live-verification.md`.
- **Compression must preserve GoalManager state:** Context compression rotates session ids, but `/goal` state is stored under `goal:<session_id>`. Before proving or resuming a live SuperGoal, follow `SessionDB.get_compression_tip(old_sid)` to the current child and verify the goal exists there. If an active goal remains only on a compression parent, migrate it to the tip or rely on `migrate_goal_to_session(old, new)`; stale parent goals must be cleared/paused so continuations do not run in the wrong session. See `references/goal-state-compression-migration.md`.
- **Restart proof must be detached:** If SuperGoal work patches gateway/GoalManager code, the live Telegram proof is invalid until the running gateway process has restarted on the patched code. Do not restart inline from the current gateway turn and do not run `hermes gateway run --replace`; schedule a delayed `systemd-run` probe that restarts the service after the answer is delivered, waits for a fresh PID, verifies GoalManager state on the visible/compression-tip session, and sends a compact proof message. See `references/gateway-restart-live-proof.md`.
- **Gateway fixes must survive Hermes updates:** If a SuperGoal launch bug required Hermes gateway/GoalManager code changes, dirty live files are not enough. Commit and push the patch to the deployment's private Hermes fork and update the Hermes private update workflow/server-doctor runbook so future upstream merges preserve the rail. See `references/supergoal-hermes-update-preservation.md`.
- **Repeated approval-blocked goal loops:** If `BLOCKED_BY_APPROVAL` repeats across `/goal` continuations, do not keep restating the approval card. Treat it as terminal blocked state, patch/mark the visible compression-tip GoalManager row as blocked, and verify any gateway restart loaded the fix. See `references/repeated-approval-blocked-goal-loop.md`.
- **Bounded manifest instead of internal approval spam:** If the user/operator asks to restart/rewrite a SuperGoal “до конца без апрувалов внутри” after prior card spam, do not treat that as unlimited consent and do not keep approval cards inside phase specs. Write one launch-level bounded manifest with exact allowed IDs/actions, explicit forbidden side effects, fresh readback gates, and terminal fail-closed blockers for anything outside scope. Add an early safety/readback phase for money/resource surfaces. See `references/bounded-manifest-no-internal-approvals.md`.

## When to deviate from the workflow

- **Very small task** (< 1 hour of work, single file): tell the user this doesn't need chip-supergoal, suggest just doing it. Don't force the machinery.
- **The user pushes back on a phase during intake**: collapse, re-plan, continue.
- **Mid-run interruption**: if the user stops the run and asks for a change, update the affected `.supergoal/phases/phase-N.md` spec, run `validate-phase.sh` on it, then ask the user to resume (they can re-dispatch the same `/goal` or just say "continue"). No need to restart phase 1.
- **Mid-phase architectural steering**: if Chip adds a constraint while a phase is in progress, do not leave it as a chat note. Patch `ROADMAP.md`, the next relevant phase spec, and a focused regression probe when the constraint is machine-checkable; record it in the current phase report and revalidate touched specs before advancing. Example: “one canonical skill with internal modes, no split `*-hermes` skill” → add a unification probe and make the regression-gate phase own cleanup.

---

## Output Contract

A successful `/chip-supergoal` planning run produces:

- `.supergoal/THINKING.md` with goals, constraints, risks, dependencies, applied memory, available tools, and `RPD_PLAN_REVIEW`.
- `.supergoal/RESEARCH.md` when research is required, recording skill `perplex` usage, queries, sources, existing-solution candidates, build-vs-buy verdict, planning implications, and unverified assumptions.
- `.supergoal/ROADMAP.md` with decision, why this path, non-goals, build-vs-buy verdict, research evidence, Architect+ lite summary when applicable, phases, dependencies, assumptions, mandatory commands, and risky-phase/RPD gate summary.
- `.supergoal/STATE.md` initialized for the future `/goal` session.
- `.supergoal/phases/phase-N.md` files with falsifiable criteria, mandatory commands, evidence requirements, dependencies, and `RPD required` / `RPD focus` metadata.
- `.supergoal/PROTOCOL.md` copied from `templates/PROTOCOL.md`, including embedded `RPD_PHASE_REVIEW` and `RPD_FINAL_REVIEW` gates for the future `/goal` runner.
- A CLI/client-safe `/goal` handoff using the `SUPERGOAL_GOAL_BODY:` reply shortcut when available.

The skill itself is plan-only. It must not claim that execution completed; only the later `/goal` session can earn `AUDIT_COMPLETE` and `SUPERGOAL_RUN_COMPLETE`.

**Hard pitfall — do not execute phases manually.** If the user/operator asks for Supergoal + `/goal`, or corrects you for acting outside it, stop all direct code edits. Do not mark phases complete, do not advance `STATE.md`, and do not present manual fixes as Supergoal progress. If useful manual changes already exist, treat them as a dirty baseline: reset/keep `STATE.md` at `READY_TO_DISPATCH` with `Completed phases: none`, then produce a `SUPERGOAL_GOAL_BODY:` that instructs the `/goal` run to audit/reconcile the dirty tree from phase 0. Only a formal `/goal` run with `SUPERGOAL_PHASE_START` / `SUPERGOAL_PHASE_VERIFY` / `SUPERGOAL_PHASE_DONE` / `SUPERGOAL_TURN_YIELD` / `AUDIT_COMPLETE` / `SUPERGOAL_RUN_COMPLETE` counts as Supergoal execution.

- **Live failure invalidates stale COMPLETE.** If a prior `.supergoal/STATE.md` says `COMPLETE` but Chip reports a real live failure that falsifies the old done criteria, do not defend the old completion or pile a workaround on top. Archive the completed artifacts under `.supergoal/archive/<old-goal>-<timestamp>/`, create a new root SuperGoal with the live incident as regression evidence, and explicitly state that the previous acceptance bar was insufficient. This is especially important for model/gateway/bot work where direct smokes can pass while the real Telegram path fails.

**Model/gateway/bot SuperGoals need highest-surface proof.** If the failure happened through a bot, gateway, model router, fusion/MoA route, or Telegram channel, generic phase criteria and direct API smokes are not enough. Use `references/model-gateway-supergoal-live-failure.md`: write incident-specific fixtures, block plugin-masked success, require stage-level billing/credit evidence, and require exact live readback or an honest approval-gated blocker before claiming “fixed”.

- **RPD-reviewed launch refresh.** If the user/operator asks for `/rpd` / xhigh review of a SuperGoal and the review mutates phase specs, assumptions, acceptance criteria, or approval gates, rewrite `LAUNCH_GOAL.md` before handing it back. The new goal body must reference the RPD report and current phase specs; otherwise Chip may start a stale launch body. See `references/rpd-reviewed-supergoal-launch.md`.

**Billable/prod resource boundary.**

**Old-server decommission after migration.** If the SuperGoal is about deleting/decommissioning a source server after a production migration, include a dedicated backup/restore-readiness phase before any drain/delete phase. For Human20/RF this means explicitly loading the Restic Doctor path (`server-doctor` → `restic-doctor` profile `human20-rf`) and requiring snapshots, lock state, repo health, fresh current-new backup, and scratch restore-smoke evidence. DNS/floating-IP success alone is not deletion readiness; phase specs must also verify new-host boot survivability, migrated maintenance timers, hidden old workloads, and a final explicit provider-delete approval gate.

**Manual provider resource adoption.** If the automated create phase is blocked by provider capacity or billing constraints and Chip creates the server/bucket/resource manually in the provider UI, do not retry creation or create a duplicate. Patch/rewrite the SuperGoal so the next phase adopts the manual resource with read-only provider evidence: exact id/name/project, region, OS/image, CPU/RAM/disk/network, preset/config id, base + attached service cost, temporary/public IPs, duplicate count, and old production IP/DNS unchanged. If provider detail APIs expose generated root passwords or bootstrap secrets, never persist or print the values; record only that such a credential exists and add a hardening gate to prove key-based access and disable password/root SSH where appropriate. See `references/manual-provider-resource-adoption.md`.

## Quick Test Checklist

- [ ] `skill_view("chip-supergoal")` loads this skill and shows `references/rpd-review-gates.md`.
- [ ] A generated phase spec validates with `scripts/validate-phase.sh` and includes `RPD required:` plus `RPD focus:`.
- [ ] Stage 3 loads/uses skill `perplex` first when available for current research; generic `web_search` is only fallback.
- [ ] Stage 6 summary includes Decision / Why this path / Non-goals / Build-vs-buy / Research evidence / Architect+ lite.
- [ ] Stage 6 summary includes an `RPD_PLAN_REVIEW` block, not the old self-critique-only block.
- [ ] `.supergoal/PROTOCOL.md` includes `RPD_PHASE_REVIEW` and `RPD_FINAL_REVIEW` without requiring any external `/rpd` skill.
- [ ] Public package scan finds no secrets, credentials, chat IDs, local runtime state, or private infrastructure details.
- [ ] The handoff remains plan-only: `/chip-supergoal` prints a `/goal` body; it does not execute project phases itself.

## Done Criteria

- [ ] Frontmatter has `name: chip-supergoal` and a trigger-rich description.
- [ ] RPD v2 behavior is embedded in this package via `references/rpd-review-gates.md` and `templates/PROTOCOL.md`, including evidence tiers, Senior Gate, overengineering budget, and anti-theater mutation rules.
- [ ] `SKILL.md` does not require loading `chip/rpd`, `/rpd`, or any private operator skill.
- [ ] All phase specs contain measurable acceptance criteria, mandatory commands, evidence requirements, and RPD metadata.
- [ ] Substantial/risky plans include source-of-truth boundary, permission matrix, failure-mode matrix, and verification strategy, or a narrow skip reason.
- [ ] Plan review mutates weak specs or records `checked-holds` with evidence.
- [ ] Public repo contents are sanitized and installable as a standalone Hermes skill.

---

## Reference files

- `references/architect-plus-lite.md` — lightweight contract-first planning gate for substantial/risky plans
- `references/category-backed-skill-path-validation.md` — validate/patch stale Hermes skill paths in phase specs; resolve category-backed `skill_dir`, avoid duplicate skills, and clean `__pycache__` after script validation
- `references/research-before-design.md` — research-before-design gate with skill `perplex` priority, RESEARCH.md schema, and existing-solutions gate
- `references/rpd-review-gates.md` — embedded RPD v2 mutation-gate contract: evidence tiers, context discipline, Senior Gate, overengineering budget, and anti-theater rules used by plan review and generated `/goal` protocol
- `references/planning-depth.md` — what makes a plan deep enough to deserve "Super"
- `references/phase-design.md` — how to slice phases that auto-chain cleanly
- `references/goal-format.md` — what `/goal` is on Claude Code + Codex, chip-supergoal's single-`/goal` shape, required transcript blocks
- `references/telegram-md-goal-launch-hardening.md` — reply-to-`LAUNCH_GOAL.md` hardening: keep `/goal` out of hydrated args, strip launch tails robustly, verify stored GoalManager body
- `references/telegram-goal-kickoff-prompt-invariant.md` — official `/goal` start hardening: queue `GoalManager.next_continuation_prompt()` as the first turn, not naked `state.goal`, for reply, button, and autodispatch rails
- `references/auth-oauth-provider-audit.md` — auth/OAuth production verification: provider buttons are not proof; verify Supabase/backend provider probes, fail closed, and treat real provider roundtrip as trust-prior unless actually exercised
- `references/model-gateway-supergoal-live-failure.md` — model/router/gateway/bot SuperGoal pitfall: direct smokes and generic criteria are insufficient; require incident fixtures, no plugin-masked success, billing/credit stage proof, and highest-surface readback.
- `references/process-integrity-production-runs.md` — production Supergoal pitfall: planning chat must not manually edit/deploy/advance phases; dirty baselines must be reconciled by the formal `/goal` run
- `references/production-deploy-gates.md` — production deploy phase gates for submodules/gitlinks, untracked product files, canonical deploy refs, runtime symmetry, and live smoke
- `references/rollout-final-audit-lessons.md` — SuperGoal rollout/final-audit lessons: exact-SHA deploy gates, code-vs-media split, token-fixture hygiene, and classifying existing production defaults
- `references/old-server-decommission-drain.md` — safe drain pattern for old/source production hosts after migration: Restic restore-readiness precondition, stop order, no provider mutation, and delete-approval rollback language
- `references/telegram-button-goal-session-key.md` — Telegram clarify button `/goal` pitfall: supergroup topic callbacks must preserve the normal `group:<chat_id>:<thread_id>` session key shape or the goal starts invisibly in a sibling session
- `references/gateway-restart-proof-and-bogus-goal.md` — gateway restart proof and bogus/stale GoalManager state recovery notes
- `references/repeated-approval-blocked-goal-loop.md` — approval-blocked SuperGoal loops: treat `BLOCKED_BY_APPROVAL` as terminal, fix visible/compression-tip GoalManager state, avoid repeating the same approval card, verify restart proof
- `references/bounded-manifest-no-internal-approvals.md` — rewrite approval-spam SuperGoals into one launch-level bounded manifest with exact IDs/actions, forbidden side effects, fresh readback gates, and no internal approval prompts
- `references/goal-state-compression-migration.md` — GoalManager state preservation across context compression: migrate active/paused/blocked goals from compression ancestors to the current session tip and clear stale parent goals
- `references/gateway-restart-live-proof.md` — safe delayed gateway restart pattern for SuperGoal/Gateway patches: detached `systemd-run`, fresh PID, health check, visible GoalManager state proof, and compact Telegram proof message
- `references/supergoal-git-cleanup-resume.md` — recovery pattern when local git cleanup/reset happens mid-SuperGoal: preserve WIP branch, reconcile ignored `.supergoal/STATE.md` with tracked deliverables, rehydrate completed-phase files minimally, and rerun focused verifiers before continuing
- `references/supergoal-hermes-update-preservation.md` — preservation checklist for Hermes gateway/GoalManager SuperGoal fixes: commit to private fork, update server-doctor/Hermes update workflow, and verify the rail after future upstream merges
- `references/phase-marker-bookkeeping.md` — recovery pattern for interrupted/compacted turns where `STATE.md` advanced but the visible transcript missed required SuperGoal markers
- `references/repeated-complete-continuations.md` — stop pattern for stale standing-goal continuations after `STATE.md` is already complete; avoid re-running audits/probes unless explicitly requested
- `references/active-supergoal-skill-review-pause.md` — pause the SuperGoal phase loop when Chip explicitly asks to review/update the skill library, update skills, then resume from `STATE.md` on the next standing-goal continuation
- `references/architecture-decision-supergoal.md` — phase pattern for SuperGoals that decide/evaluate architecture rather than ship a feature: baseline ledger, SSOT update, eval harness, sandbox probes, heavy-candidate eligibility gate, polish/harden
- `references/supergoal-status-snapshots.md` — Project Flow-style human-readable `SUPERGOAL_STATUS` snapshots for phase/audit progress without importing Project Flow rails or supervisor machinery
- `references/skill-package-sync.md` — publishing checklist for chip-supergoal package edits: focused copy, validation, secret scan, private repo + powerpack nested sync, and public sanitation guard

## Scripts

- `scripts/detect-stack.sh` — identifies language, package manager, framework, build/test/lint commands (brownfield)
- `scripts/detect-env.sh` — greenfield environment recon
- `scripts/summarize-repo.sh` — compressed repo map (brownfield)
- `scripts/validate-phase.sh` — checks a phase spec has exact required sections, metadata, RPD fields, and non-placeholder content
- `scripts/test.sh` — public package regression suite for install layout, privacy scan, phase validation, and repo-state audit guards

## Templates

- `templates/ROADMAP.md` — phase plan with dependencies
- `templates/STATE.md` — live progress file
- `templates/RESEARCH.md` — research-before-design record schema
- `templates/phase-goal.txt` — phase spec skeleton (work, criteria, evidence, mandatory commands)
- `templates/PROTOCOL.md` — phase execution loop, failure recovery, memory writeback (copied to `.supergoal/PROTOCOL.md` at dispatch)
