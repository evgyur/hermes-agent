---
name: create-skill
description: "Builds, evaluates, or refactors a skill into a concise, testable structure with explicit contracts, strong trigger descriptions, and compatibility mapping. Use for /create-skill extract, interview, refactor, or compare, especially when turning a workflow or conversation into a reusable skill, tightening weak trigger quality, splitting a bloated skill into router + references, or comparing old/new skill behavior without guessing."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [skills, authoring, evaluation, refactor]
    related_skills: [hermes-agent-skill-authoring]
---

# /create-skill

> **Hermes-native rule:** this skill must follow Hermes Agent's skill system, not legacy OpenClaw/Shaw assumptions.
> - Runtime/user skills live under `~/.hermes/skills/` and are created or updated with `skill_manage`.
> - Bundled or in-repo Hermes skills live under the active Hermes checkout (`skills/` or `optional-skills/`) and require normal repo editing + validation + git commit.
> - External skill directories are read-only discovery sources; local `~/.hermes/skills/` wins when names collide.
> - Legacy OpenClaw/Shaw paths such as `/opt/clawd-workspace`, `skills/secret`, and mandatory `/shaw` routing are not valid defaults in Hermes. Treat them only as migration/audit evidence, never as a required implementation path.

Системный скилл для сборки качественных Hermes skills по best-practices (concise + progressive disclosure + validation loop).

## ⚠️ Critical Rule: Use Hermes skill primitives for implementation work

**Любое создание или изменение skill должно начинаться с выбора target scope:**

1. `user-local` / agent procedural memory → use `skill_manage(action=create|patch|edit|write_file|remove_file)` under `~/.hermes/skills/`.
2. `bundled` / in-repo Hermes skill → edit the active Hermes repo (`skills/` or `optional-skills/`), validate, test, and commit.
3. `external read-only` / public reference skill → clone or inspect in temp first; do not install into the current agent unless the current runtime is the intended target or the user explicitly asks.
4. `compare/evaluate-only` → do not edit; return verdict + evidence.
5. Migrated OpenClaw/Shaw skill → first run the Hermes migration audit: identify `/shaw`, `/opt/clawd-workspace`, `.openclaw-*`, legacy persistence guards, and replace them with Hermes equivalents before recommending it as canonical.

Не делать ничего в лоб или без структуры. Порядок:

1. `/create-skill` определяет **что** нужно (mode, scope, Output Contract)
2. Выбирается target scope: `user-local`, `bundled/in-repo`, `external read-only`, или `compare-only`
3. Для `extract|interview|refactor` реализация идёт через Hermes-native path (`skill_manage` для user-local; repo edit + validator/tests для bundled)
4. Для `compare` без изменений — evaluation-only маршрут с verdict + evidence
5. После каждого изменения — persistence protocol (validate → verify discovery/loading → commit if repo-backed)

## Trigger
- `/create-skill extract` — из готового контента или текущего диалога сделать production-ready skill.
- `/create-skill interview` — собрать skill через 8 уточняющих вопросов, сначала вытащив максимум из контекста.
- `/create-skill refactor` — разрезать legacy skill на router + modules.
- `/create-skill compare` — сравнить old/new версию skill без обязательного изменения.
- Использовать и для усиления trigger quality, упрощения wording, добавления iteration loop и сравнения old/new поведения существующего skill.
- Типовые пользовательские фразы: "сделай из этого skill", "упакуй это в reusable workflow", "наш skill плохо триггерится", "разрежь этот SKILL.md", "сравни старую и новую версию skill", "не меняй skill, только оцени".

## Input
- Mode: `extract | interview | refactor | compare`
- Source: текст/файлы/путь к скиллу
- Для `compare`: `old source`, `new source`, цель сравнения
- Ограничения: backward compatibility, naming, сроки

### Default mode selection
- Если пользователь приносит готовый диалог, заметки, транскрипт или процесс и просит "сделать из этого skill" без явного mode → выбрать `extract`.
- Если пользователь описывает существующий skill как слишком большой, плохо триггерящийся, просит разрезать его и сохранить совместимость → выбрать `refactor`.
- Если пользователь просит только сравнить старую и новую версию без изменений → выбрать `compare`.

## Output Contract (mandatory)
Верни mode-specific артефакты:

### `extract`
1. `skill folder name`
2. `SKILL.md` (router или single-purpose)
3. `references/*` список (один уровень от SKILL.md, без deep nesting)
4. `quick test checklist`
5. `manual review checklist`
6. `operating spec`:
   - cadence
   - sources
   - section schema
   - owner/operator steps
   - done criteria
7. `assumptions + gaps` (если источник неполный)

### `interview`
1. `skill folder name`
2. `SKILL.md`
3. `references/*` список
4. `quick test checklist`
5. `manual review checklist`
6. `open questions resolved via 8-question interview`

### `refactor`
1. `skill folder name`
2. `SKILL.md` (router)
3. `references/*` список и/или `modules/*`
4. `quick test checklist`
5. `manual review checklist`
6. `split plan`
7. `backward-compat map`
8. `compat shims / preserved entrypoints`
9. `migration notes`

### `compare`
1. `verdict`
2. `what improved`
3. `what regressed`
4. `must-fix`
5. `confidence/evidence`
6. `recommended next step` (`keep`, `revise`, `reject`, `needs more tests`)

Рекомендуемый handoff-довесок:
- `evaluation note` — какой режим выбран (`old/new`, `with/without`, `review-only`), почему, и где evidence слабое. Для implementation modes можно дополнительно указать, что улучшили после первой итерации.
- `plan review note` — какие personas были включены, какие возражения нашли, что поменяли в плане, и почему `ready to write = yes`.

## Embedded Reasoning Personas Planning Gate

Ниже reasoning-personas встроен прямо в этот skill. Не требуй отдельный submodule/reference для planning gate — используй этот встроенный блок.

### Core Concept

Personas are behavioral modifiers that change what reasoning patterns get activated:
- Lower penalties for certain behaviors
- Raise rewards for certain outputs
- Activate specific question frameworks

### Quick Reference

#### Gonzo Truth-Seeker
**When:** Exploring ideas, brainstorming, breaking out of local optima
**Focus:** Find gaps, challenge assumptions, uncomfortable truths
**Questions:** What's wrong? What's missing? What assumption is everyone making?

#### Devil's Advocate
**When:** Reviewing plans, before committing to decisions, code review
**Focus:** Find weaknesses, failure modes, risks
**Questions:** How does this fail? What's the weakest link? What happens at 10x scale?

#### Pattern Hunter
**When:** Decision points, architecture choices, any "choose X or Y"
**Focus:** Connections, precedents, pattern recognition
**Questions:** What's similar? Have we decided this before? What did we learn last time?

#### Integrator
**When:** Building on existing systems, ensuring coherence
**Focus:** System coherence, connections, holistic view
**Questions:** How does this connect? What else is affected? Second-order effects?

### Persona Process

1. **Identify context** — what type of thinking is needed?
2. **Activate persona** — silently use the right question framework.
3. **Apply questions** — run through the persona checks on the proposed plan.
4. **Output** — return only compact planning findings, not theatrical roleplay.

### Auto-Activation Map for create-skill

| Mode | Default Personas |
|------|------------------|
| `extract` | Pattern Hunter + Integrator + Gonzo Truth-Seeker |
| `interview` | Pattern Hunter + Gonzo Truth-Seeker |
| `refactor` | Pattern Hunter + Devil's Advocate + Integrator |
| `compare` | Devil's Advocate + Pattern Hunter |

### Manual Triggers

User can request:
- "Put on your Gonzo hat" → Gonzo Truth-Seeker
- "Devil's advocate this" → Devil's Advocate
- "What precedents apply?" → Pattern Hunter
- "How does this fit with everything?" → Integrator
- **"Тащи шапки"** → Multi-Persona Analysis (все 4 шапки по очереди)

### Multi-Persona Analysis

For thorough analysis, cycle through:
1. **Pattern Hunter** — Context and precedents
2. **Gonzo Truth-Seeker** — Novel insights
3. **Devil's Advocate** — Failure modes
4. **Integrator** — System coherence

Use this full cycle only when the plan is unusually ambiguous, risky, or high-impact. Default to the mode-based subset above.

### Planning Gate Output Contract

Return a short block:
- `proposed plan`
- `persona findings`
- `plan revisions`
- `ready to write: yes/no`

If `ready to write != yes`, drafting/editing must not start.

### Short Planning Template

```md
Plan review note
- personas: Pattern Hunter, Devil's Advocate
- proposed plan: ...
- key objections: ...
- plan revisions: ...
- ready to write: yes
```

## Workflow (copy as checklist)
```md
Create-Skill Progress:
- [ ] 1) Confirm mode + scope
- [ ] 2) If mode is implicit, infer `extract` from ready source material, `refactor` from bloated/undertriggered existing-skill repair, or `compare` from evaluation-only intent
- [ ] 3) Extract what is already known from the conversation/source
- [ ] 4) For `refactor`, inventory legacy interface before changes
- [ ] 5) Build a short proposed plan before any draft/edit
- [ ] 6) Run the embedded reasoning-personas planning gate on the plan
- [ ] 7) Revise the plan and decide `ready to write = yes/no`
- [ ] 8) Choose Hermes target scope and implement via `skill_manage` (user-local) or repo edit + validation/tests (bundled/in-repo)
- [ ] 9) Draft concise SKILL.md or compare verdict, depending on mode
- [ ] 10) Move heavy content to references/* and/or modules/* when refactor needs modular split
- [ ] 11) Choose benchmark mode (`old/new`, `with/without`, or `review-only`) and record why
- [ ] 12) Add 2-3 realistic test prompts + review checklists when implementation work exists
- [ ] 13) Run one iteration loop: review -> rewrite -> re-check when implementation work exists
- [ ] 14) Run Hermes persistence protocol (validate -> verify discovery/loading -> commit if repo-backed) after edits only
- [ ] 15) Validate naming/frontmatter/compat
- [ ] 16) Return full Output Contract
```

### Step rules
1. **Scope first**: один скилл = одна цель.
2. **Conversation first**: перед вопросами вытащи из диалога/источника уже известные шаги, input/output, правки и критерии успеха.
3. **Mode defaulting matters**: если есть готовый материал и просьба "сделай из этого skill" — это `extract`; если существующий skill описан как слишком большой, слабо триггерящийся и требующий сохранения совместимости — это `refactor`; если просят только оценить old/new без изменений — это `compare`.
4. **Planning gate before writing**: до любого draft/edit сначала собрать короткий proposed plan, прогнать встроенный reasoning-personas блок, пересобрать план и только потом писать.
5. **Implementation via Hermes-native path**: создание или изменение skill идёт через `skill_manage` для user-local skills, либо через repo edit + validation/tests + commit для bundled/in-repo skills. `compare` без изменений может идти без implementation path.
6. **Trigger quality matters**: description должна объяснять не только что делает skill, но и когда он должен сработать; лучше чуть переобъяснить триггеры, чем получить undertrigger.
7. **Audience-adaptive wording**: подстраивай язык под уровень пользователя; не сыпь jargon вроде `JSON`, `assertions`, `benchmark`, если это не помогает.
8. **Concise core**: SKILL.md только orchestration и ключевые команды.
9. **Progressive disclosure**: детали в `references/*.md`, всё с прямыми ссылками из SKILL.md. Harness Engineering rule: root skill files should be tables of contents and contracts, not encyclopedias; if a section grows into operational detail, extract it into a reference and keep the trigger/contract visible.
10. **Right degree of freedom**:
   - high: общие эвристики (review/analysis),
   - medium: шаблон/псевдокод,
   - low: хрупкие операции (exact commands).
11. **Persona mapping by mode**: используй встроенный reasoning-personas блок ниже.
   - `extract` → Pattern Hunter + Integrator + Gonzo
   - `interview` → Pattern Hunter + Gonzo
   - `refactor` → Pattern Hunter + Devil's Advocate + Integrator
   - `compare` → Devil's Advocate + Pattern Hunter
12. **Planning gate contract**: вернуть коротко `proposed plan`, `persona findings`, `plan revisions`, `ready to write: yes/no`. Если `ready to write != yes`, drafting/editing не начинать.
13. **Extract from conversation**: для `extract` явно собрать цель, cadence, inputs/sources, output sections, operator steps, done criteria, assumptions/gaps. Если исходник неполный — всё равно вернуть usable draft + assumptions + deferred questions.
14. **Refactor starts with inventory**: до перепаковки зафиксировать legacy commands, modes, outputs, trigger phrases и inbound links; потом уже делать split plan и compat strategy. If the refactor is Shaw/XHigh + repo-backed, finish to the real closure gate: canonical contract, stale-policy/deterministic probe, aggregate verifier + CI wiring, live-skill sync, commit/push, remote-head and CI proof. Do not stop after the first partial cleanup when the user asked to refactor and push.
15. **Pipeline skill refactor rule**: если bloated skill описывает не только стиль/процедуру, а целый pipeline (intake → source/state → generation → gates → delivery/verify → revision/publish), не лечить это простым сокращением `SKILL.md`. Сначала разделить слои: root/router contract, policy references, runtime scripts, task state, artifacts/cache, regression corpus. До удаления media/artifacts явно классифицировать protected templates/brand assets/golden samples and write a retention policy. Убрать duplicate canonical modules через shim/rename до prose split. Использовать `references/pipeline-skill-refactor.md`.
    - Для Hermes prompt-budget/skills-index refactors сначала проверь, что именно попадает в стартовый prompt. Hermes индексирует только metadata и режет `description` примерно до 60 символов, поэтому массовое укорочение frontmatter descriptions почти не экономит токены и может ухудшить discovery. Для сжатия стартового контекста предпочитай category names-only compaction / platform toolsets / prompt-builder changes, а не переписывание самих skill bodies.
    - Если nested compatibility module всё ещё содержит `SKILL.md` с colliding `name:`, простая смена frontmatter может не снять ambiguity. Для shim, который не должен грузиться как отдельный skill, заменить loadable `SKILL.md` на `README.md`/`SHIM.md` и проверить `skill_view(<canonical>)`.
    - Для artifact-heavy skill trees сначала вынести generated media/cache из skill dir и оставить compatibility symlink, если скрипты ждут старый путь. Не смешивать procedural memory и generated outputs.
    - Для split-entrypoint refactors запрещай параллельные skill names вроде `<skill>-hermes`, `<skill>-codex`, `<skill>-openclaw`, если пользователь хочет один canonical skill. Делай один root skill с internal `mode`/`runtime surface`, чисти активные docs/schemas/fixtures от split-name directions, и добавляй standalone unification probe. Exclude только historical state/backups/quarantine/SHIM; active docs, schemas, fixtures и Powerpack pointers должны быть зелёными. При массовой замене старого имени не делай слепой global replace внутри нового canonical skill: preserve `metadata.compatibility.replaces`, historical migration notes, legacy reference filenames, and “old X → this skill” wording, otherwise the new skill will claim it replaces itself.
16. **Benchmark mode first**: до финальной оценки выбрать один из режимов — `old/new` по умолчанию для `compare` и часто для `refactor`, `with/without` для новых проверяемых workflow-skills, `review-only` для субъективных или дорогих кейсов. Для `compare` отклоняться от `old/new` только если нет надёжного baseline. Причину выбора зафиксировать в handoff.
16. **Iteration loop by default**: после первого draft добавить 2-3 realistic test prompts, сделать review и переписать skill до финальной валидации. Пропуск допустим только если пользователь явно отказывается, mode=`compare`, или skill слишком субъективный для честного benchmark.
17. **Compare-only fast path**: если задача чисто оценочная, не требуй новый `SKILL.md`, rewrite loop или implementation ritual; верни verdict по compare contract с evidence и next step.
18. **Hermes migration audit**: если skill пришёл из OpenClaw/Shaw, проверить и убрать legacy coupling: `/shaw`, `/opt/clawd-workspace`, `skills/secret`, `.openclaw-*`, custom seal/verify guards. Заменить на Hermes paths, `skill_manage`, `/home/hermes/.hermes/skills/create-skill`, `hermes skills ...`, и стандартную валидацию.
20. **Post-session skill-library review**: when asked to review a finished conversation for skill updates, be active. Prefer patching a currently loaded/class-level skill over creating narrow one-off skills. Capture durable workflow corrections, reusable validation/push patterns, style complaints, missing skill steps, or non-trivial techniques. Do not save transient setup failures or one-off task narratives. If a support file is added, add a one-line pointer from SKILL.md so future agents can discover it. If the target root SKILL.md is oversized and cannot be patched, still write the support file, then patch the closest loaded governing skill with the discoverability note and report the oversized-root blocker honestly. If the session changed a source-of-truth contract (for example, a canonical repo/skill that other skills must follow), add or tighten the propagation checklist in the governing class-level skill, not just the component skill. If the correction is “make this custom workflow work through the upstream/native primitive” (for example a planner compiling into standard `/goal` rather than a custom runner), encode an explicit compatibility boundary in the component skill, add judge/contract probes, and make the launch artifact self-contained for the native primitive instead of creating a parallel orchestration rail.

For research-to-skill-library SuperGoals, use the `chip-supergoal` support pattern at `references/research-to-skill-library-supergoal.md` when available: archive stale `.supergoal`, copy research into `.supergoal/research-sources/`, make Phase 0 source-lock/source-quality, target class-level skills first, and describe zero-indexed phases as “N phases, indexed 0–N-1” in summaries. For Chip handoffs, make the assistant Markdown reply itself a clean `Goal:` manifest that can be replied to/launched; do not only provide copy-this `/goal` instructions. See `references/supergoal-md-launch-handoff.md`. If the session already patched the obvious operational skill before the post-session review, do not invent a narrow duplicate; instead verify that the loaded umbrella has both (a) a root pointer and (b) the detailed `references/` runbook, then update the meta/governing skill only if the review workflow itself learned something. If a user explicitly asks for Shaw/XHigh to fix a skill after a source-distortion complaint, treat the source-selection/version-migration runbook as the durable class-level update: canonical source first, legacy/reference split, support-file pointer, then validation. For repo-backed skills, finish with the repo hygiene loop: focused tests or guard, `git diff --check`, commit, push, remote HEAD verification, CI/run status check, and clean `git status` unless the user explicitly wants local-only edits. If the aggregate verifier fails, first run the same verifier on a clean `origin/main` checkout: if the failure is pre-existing/baseline-red, do not weaken tests or block the useful patch; record the baseline failure, run the non-failing verifier sections plus focused contract assertions and changed-file secret/privacy scans, then patch the governing runbook with that fallback. If the live skill dir is not the git repo, use the temp-clone sync pattern in `references/private-repo-backed-skill.md` instead of staging from the wrong directory. When several loaded skills share the contract, patch all governing skills that future agents may load independently, not only the one that failed first.
21. **Skill safety preflight for new/external skills**: when designing, importing, publishing, or installing skills from community/external sources, treat security scanning as part of the skill workflow, not an optional afterthought. Use the existing Hermes `tools/skills_guard.py` quarantine model where available, and consider NVIDIA SkillSpector as an additional CI/local scanner for broader agent-skill risks. A good gate scans: prompt injection, hidden instructions, data exfiltration, credential/file access, network/script side effects, persistence, memory poisoning, dependency/CVE/supply-chain issues, obfuscation, and excessive agency. Recommended verdicts: `safe` passes, `caution` requires human/manual review, `dangerous` blocks install/merge unless explicitly forced by the owner. Do not rely on an LLM-only review; combine deterministic static checks, script/AST audit, dependency scan, declared permissions/toolsets, and optional semantic review.
22. **Money-skill permission manifests and probes**: when a skill can route toward wallets, trading, payments, approvals, credentials, API keys, or irreversible money movement, require an explicit permission manifest before treating it as production-safe. Use `references/money-skill-permission-manifest.md` for allowed tools/files/endpoints, forbidden mutation surfaces, exact approval semantics, secret rules, and output mode labels. Pair it with `references/deterministic-money-skill-regression-probes.md` when the session adds or repairs money-skill guardrails: terminology, raw action surfaces, mutation-as-readonly paths, blanket approvals, and secret-shaped tokens must be mechanically checked, not only described in prose.

## Public multi-file skill hardening

When a skill is intended for a public repo or external install, run the additional checklist in `references/public-skill-hardening.md`. It covers raw-URL install traps, privacy scanner self-blindness, recon-script path leaks, template/validator drift, placeholder false-greens, deliverable audit false-greens, and a minimum no-dependency CI suite.

## Quick Test Checklist
- [ ] `/create-skill extract` по умолчанию выбирается для запроса вида "сделай из этого диалога skill"
- [ ] `/create-skill extract` возвращает mode-specific output, включая `assumptions + gaps` для неполного источника
- [ ] `/create-skill interview` сначала извлекает известный контекст, а потом задаёт ровно 8 вопросов до сборки
- [ ] До любого draft/edit проходит planning gate с personas и `ready to write = yes`
- [ ] `/create-skill compare` умеет вернуть verdict без требования писать новый `SKILL.md`
- [ ] Для запроса "сравни старую и новую версию skill" `compare` по умолчанию выбирает benchmark mode `old/new`
- [ ] Draft включает 2-3 realistic test prompts для первой итерации review/rewrite, когда есть implementation work
- [ ] Description объясняет и функцию skill, и trigger contexts / типовые фразы пользователя
- [ ] Выбран и назван benchmark mode: `old/new`, `with/without`, или `review-only`
- [ ] Сделан минимум один iteration loop, если он не был явно waived или mode не `compare`
- [ ] `/create-skill refactor` по умолчанию выбирается для bloated/undertriggered existing-skill repair
- [ ] `/create-skill refactor` включает legacy inventory + compat map + migration notes
- [ ] Pipeline refactor checks duplicate loadable skill names, artifact-heavy dirs, canonical/shim boundaries, and regression gates before shrinking root prose
- [ ] Implementation used the correct Hermes target path (`skill_manage` for user-local; repo edit + validation/tests for bundled/in-repo)
- [ ] For post-session reviews, every loaded governing skill that may be used independently carries the durable correction, not only the component that happened to fail.
- [ ] For repo-backed skill publication, named canonical GitHub targets override stale live-skill remotes; use `references/private-skill-repo-sync.md` and verify fresh clones before claiming push complete.
- [ ] If a repo/skill has an aggregate verifier, every new regression/probe script is wired into that verifier; standalone passing scripts are not treated as sufficient.
- [ ] Новый/обновлённый skill проходит Hermes validation/frontmatter checks and any bundled helper guard

## Done Criteria
- [ ] YAML frontmatter валиден (`name`, `description`) там, где создаётся/редактируется skill
- [ ] Description в 3-м лице, с чёткими trigger contexts и типовыми ситуациями применения
- [ ] До уточняющих вопросов извлечён максимум полезного из уже данного контекста
- [ ] До любого writing/editing пройден planning gate с personas и получено `ready to write = yes`
- [ ] SKILL.md короткий и директивный там, где нужен новый или обновлённый skill
- [ ] references вынесены и перечислены
- [ ] Есть 2-3 realistic test prompts, quick tests и manual review для implementation modes
- [ ] Выбран и задокументирован benchmark approach (`old/new`, `with/without`, `review-only`)
- [ ] Для `extract` есть usable draft, operating spec и `assumptions + gaps`, если источник неполный
- [ ] Для `refactor` есть legacy inventory, split plan, compat map и migration notes
- [ ] Для `compare` есть verdict, regressions/improvements, evidence и fast-path без лишнего implementation ritual
- [ ] Первый draft был reviewed, и сделан минимум один rewrite pass, если loop не waived явно
- [ ] **Имплементация через Hermes-native path** — не через legacy `/shaw` или `/opt/clawd-workspace`, если mode не `compare`

## Guardrails
- **НИКОГДА не создавать или менять скилл от балды** — сначала выбрать mode + scope, затем использовать Hermes-native implementation path
- Не выдумывать команды/интеграции.
- Do not overclaim skill creation as product/runtime implementation. A skill scaffold, reference pack, or governing contract is `skill-scaffold`/`governance`, not “feature integrated”, “system deployed”, or “runtime implemented”. Report exact boundary: created/validated/loaded vs committed/deployed/executed.
- Не ломать публичный интерфейс без legacy inventory + compat strategy.
- Не притворяться implementation-задачей, если пользователь просит только compare/evaluate verdict.
- Не прятать критичные инструкции в nested references.
- Before publishing skill content in a public GitHub repo, use `references/public-skill-repo-publication.md`: verify no secrets/private paths, add extensioned wrappers for extensionless executable scripts so Hermes linked-file discovery surfaces them, fresh-clone test the repo, wait for CI success, and clean stale default branches.
- If the skill is a public wrapper around a private service/API-key setup flow, also use `public-fork-distribution/references/service-setup-skill-publication.md`: ship a deterministic installer, keep secrets in local `.env`, use `key_env`/helper indirection, test the privacy boundary with fake token-shaped fixtures, and report `no CI ran` when no GitHub Actions workflow exists.
- When reviewing an external skill repo plus an upstream bundled-skill PR, treat it as `compare/evaluate-only` unless the user explicitly asks to edit. Inspect both the standalone skill and the PR diff/tests; if the PR URL is truncated, search GitHub for the skill name in the target repo before asking. Run the skill workflow guard when available, but report guard findings as quality-bar suggestions rather than blockers if upstream does not require that structure. Always look for over-broad aliases/triggers, delegate/tool safety caveats, discoverability metadata, and test coverage that asserts the new contract.
- Не завершать без validation loop или явного compare-only evidence.

## Persistence Protocol (mandatory after any edit)

После изменения skill обязательно выполнить Hermes-native проверку:

1. **Validate frontmatter and size**: `name`, `description` (≤1024 chars), body after frontmatter, total content ≤100k chars.
2. **Run helper guard when available**:
   ```bash
   python3 /home/hermes/.hermes/skills/create-skill/scripts/skill_workflow_guard.py <target-skill-dir>
   ```
   If `/home/hermes/.hermes/skills/create-skill` is not available in the shell, use the absolute skill directory reported by `skill_view`.
   If validating helper scripts with `python3 -m py_compile`, remove generated `__pycache__/` before finishing; bytecode caches are build residue, not skill artifacts.
3. **Verify discovery/loading**: `skill_view(name)` for the edited skill, or `hermes skills list` / `/reload-skills` in a new session when validating CLI/gateway discovery.
4. **Repo-backed skills only**: run relevant tests and commit the change in the active Hermes repo. If the repo/skill has an aggregate verifier, wire any new regression/probe scripts into it before treating verification as complete. User-local `~/.hermes/skills/` edits do not need an OpenClaw-style seal step.
5. **Categorized/path-backed skill targeting pitfall**: if `skill_view` successfully loads a nested/category/path-backed skill but `skill_manage(name=...)` cannot resolve either its short name or qualified category/name, do not claim the learning is unsaveable and do not create a duplicate one-off skill. Use the `skill_dir`/`path` returned by `skill_view` to choose the exact editable target, apply the smallest safe file edit through the available file-editing path for that session, then verify by re-reading/loading the changed skill. If the user/tool policy for the current turn permits only skill-management tools and forbids file edits, report that exact blocker instead of inventing a duplicate skill or silently skipping the learning.
6. **Oversized SKILL.md patch pitfall**: if a loaded class-level skill is already over the skill-management size limit and `skill_manage(action=patch)` refuses to edit `SKILL.md`, do not create a narrow duplicate skill. Prefer writing a concise support file under `references/` with the durable lesson, then patch another loaded governing skill that can carry the discoverability/workflow rule. In the final reply, state that the oversized root needs a future slimming pass so the new reference can be linked from SKILL.md.
7. **Legacy/class-level format caveat**: the helper guard is strict for newly-authored `/create-skill` outputs. Older or class-level operational skills may intentionally lack `Output Contract`, `Quick Test Checklist`, or `Done Criteria`. If the guard fails only on missing create-skill-era sections while frontmatter, references, and loadability are valid, do **not** bloat the skill with fake sections just to turn the guard green. Record the guard result as non-applicable, then verify with structural checks: YAML/frontmatter, size, linked-file presence, `skill_view` load, and exact pointer/content reads. When this happens inside a SuperGoal phase, classify it as a `non-blocking legacy-format caveat`, keep the useful patch, and require phase evidence from `skill_view`, linked-file readback, exact content checks, changed-file secret scan, and preflight. See `chip-supergoal/references/legacy-skill-phase-validation.md` for the phase-report shape.
7. **New-skill exact-section pitfall**: for freshly authored skills, the guard expects exact headings like `## Output Contract`, `## Quick Test Checklist`, and `## Done Criteria`. If a new skill fails only because it used lower-case variants such as `## Done criteria`, patch the headings instead of treating the guard as optional.
8. **Secret-scan placeholder pitfall**: when validating an existing umbrella skill, broad scans across all historical references may flag placeholder env names such as `PRIVY_APP_SECRET=<...>`. Classify those as placeholder documentation only after inspecting them; still scan the newly changed files for actual secret-shaped values (raw private keys, live tokens, JWTs, provider secrets) and report the distinction honestly. Do not weaken the skill just to remove useful placeholder examples.
9. **Migration note**: if the source skill mentions OpenClaw/Shaw, record that legacy paths were audited and either removed or explicitly marked as compatibility-only.

## Hermes Migration Audit

When reviewing or refactoring a skill imported from OpenClaw/Shaw, load [Hermes migration audit](references/hermes-migration-audit.md) and check for legacy coupling before editing.

## References
- [Harness Engineering notes](references/harness-engineering.md)
- [Mode details](references/modes.md)
- [Benchmarking and eval policy](references/evals.md)
- [Description quality guide](references/description-quality.md)
- [Manual review checklist](references/review-checklist.md)
- [Enforcement / Hermes validation](references/enforcement.md)
- [Hermes migration audit](references/hermes-migration-audit.md)
- [Pipeline skill refactor pattern](references/pipeline-skill-refactor.md)
- [Artifact retention during refactor](references/artifact-retention-during-refactor.md)
- [Public skill repo publication checklist](references/public-skill-repo-publication.md)
- [Skill safety preflight](references/skill-safety-preflight.md) — security gate pattern for new/external/community/agent-created skills using Hermes skills_guard, SkillSpector, static/script/supply-chain checks, and safe/caution/dangerous verdicts.
- [Money-skill permission manifest checklist](references/money-skill-permission-manifest.md) — required manifest pattern for skills that touch wallets, trading, approvals, credentials, payment rails, or irreversible money movement.
- [SuperGoal final audit packaging](references/supergoal-final-audit-packaging.md) — stable final bundle pattern: exclude `final-audit.md` and the sidecar manifest from the zip, write package manifest, patch audit metadata, and verify bundle hash/state markers before claiming completion.
- [Research correction propagation](references/research-correction-propagation.md) — when the user corrects a source/geography/account/API assumption during research-to-skill or SuperGoal planning, patch every durable artifact and force a source-lock recheck instead of only fixing the final chat answer.
- [Deterministic money-skill regression probes](references/deterministic-money-skill-regression-probes.md) — scanner pattern for money skills: negative classes, forbidden-context handling, synthetic self-tests, secret-fixture self-contamination avoidance, and SuperGoal evidence requirements.
- [Powerpack skill submodule/direct packaging](references/powerpack-skill-submodule-packaging.md) — when adding skills into `human20team/hermes-agent-powerpack`: choose submodule vs direct bundled copy, scrub private-heavy local skills into child repos, validate, staged secret scan, fresh clone, recursive submodule init, and honest CI reporting.
- [Private repo-backed skill workflow](references/private-repo-backed-skill.md)
- [Private skill repo sync pattern](references/private-skill-repo-sync.md) — clean-clone sync/push pattern when a live skill dir points at a legacy remote but the user names canonical GitHub targets
- [Principal+ skill refactor pattern](references/principal-skill-refactor.md) — shrink an oversized incident-patch skill into a controller root + canonical references, archive the monolith, single-source launch markers, and add architecture regression checks.
- [External repo as a skill submodule](references/external-repo-submodule-integration.md) — how to vendor an external repo under an existing class-level skill when the target skill dir is not a git repo
- [External upstream skill-pack import](references/external-upstream-skill-pack-import.md) — user-local import pattern for third-party repos that ship `skills/<name>/SKILL.md`: inspect temp clone, select class-level skills, avoid over-trigger helper skills, patch minimal Hermes sections, add provenance, verify discovery/loading.
- [SuperGoal Markdown launch handoff](references/supergoal-md-launch-handoff.md) — when a skill-authoring/research SuperGoal produces `LAUNCH_GOAL.md` for Chip, make the assistant Markdown reply itself a clean `Goal:` manifest rather than copy-this-command prose.
- [Anthropic alignment notes](references/anthropic-best-practices.md)
- [Template](templates/SKILL.template.md)
