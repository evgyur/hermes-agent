# Deterministic money-skill regression probes

Use this pattern when updating Hermes skills that govern wallets, trading, approvals, API keys, payments, or irreversible money movement.

## Purpose

Prose guardrails are not enough for money skills. Add a small deterministic probe that fails if future edits reintroduce known-bad wording or unsafe surfaces.

Good probe scope:
- active root `SKILL.md` files;
- newly added/changed `references/*.md` manifests and runbooks;
- governing skill references such as `create-skill` money-skill checklists.

Avoid scanning stale archives unless the task is explicitly archival hygiene; old examples often contain intentionally bad text.

## Required negative classes

At minimum, test for these five classes:

1. **Bad account terminology**
   - Example: spot/perp readback surfaces described as separate user accounts.
2. **Broad raw action surfaces**
   - Example: `raw_request`, `trade(any_payload)`, `sign(any_payload)`, `approve(any_payload)` as a recommended interface.
3. **Mutation endpoint as read-only**
   - Example: Hyperliquid `/exchange` described as an allowed read-only probe.
4. **Blanket approval semantics**
   - Example: `go`, `continue`, `done`, or blanket approval accepted for unspecified live side effects.
5. **Secret-shaped material**
   - Example: private keys, provider tokens, bearer JWTs, long raw signed payload hex, full auth headers.

## Context classifier

Money-skill docs often contain forbidden examples and negative prompts. The probe should allow unsafe strings only when the nearby context clearly marks them as forbidden/negative.

Allow contexts containing markers such as:
- `forbidden`
- `do not`
- `never`
- `negative`
- `trap`
- `expected:`
- `bad wording`
- `fail closed`
- `not allowed`

Block the same string when it appears as a positive recommendation, allowed endpoint, implementation instruction, or generic workflow step.

## Self-test pattern

Include synthetic negative cases in the probe itself and assert every detector fires. This prevents a probe from going green because the regex is dead.

Example self-test table:

```text
case: separate spot/perp account framing -> separate_spot_perp_account_framing
case: raw request surface -> broad_raw_action_surface
case: /exchange as read-only -> exchange_as_readonly_or_allowed
case: blanket approval -> blanket_approval_semantics
case: secret-shaped token -> secret_shaped_token
```

## Avoid scanner self-contamination

If the probe contains secret-shaped synthetic fixtures, the repository/changed-file secret scan may flag the probe itself.

Safer options:
- split token-shaped strings across literals, e.g. `"Bearer " "eyJ..."`;
- use obviously fake placeholders with angle brackets, e.g. `Bearer <JWT>`;
- or exclude only the exact synthetic fixture line in the cleanliness scan, not the whole probe file.

Do **not** put real-looking live tokens, long hex signed payloads, or private-key blocks in a probe fixture.

## Evidence to save in a SuperGoal phase

Save:
- probe script path;
- probe JSON output under `.supergoal/evidence/phase-N/`;
- negative-case results;
- pointer checks showing root `SKILL.md` files link the manifests/references;
- changed-file secret scan output;
- mandatory preflight output.

If the repo is not a git repository and changed/added-line evidence is empty, do not treat that as enough. Run an explicit scan over the actual changed files and report the no-git fallback honestly.

## Completion rule

A money-skill regression phase is not complete until:
- probe exits `0` on current active skills;
- every required negative case fires in self-test;
- manifests are linked from root skills;
- changed-file secret scan is clean;
- the final report states that no live wallet/signing/trading/approval/credential side effect occurred.
