# Stale phase-plan correction: verify canonical routes

Use this when executing a SuperGoal phase that references an older page, command, route, or rail name.

## Lesson

A phase file can be stale even when `.supergoal/STATE.md` is current. During Human20 payment work, Phase 5 said to mirror `/payment_new`, but current code had already retired old NPD QR flows and made `/payment_new` a redirect-only legacy alias. The canonical buyer flow was `/payment`.

## Required step before implementing a phase from old wording

Before coding from a phase instruction that names an existing route or module:

1. Search current code for the named route/module and its tests.
2. Identify whether it is canonical, alias/redirect, deprecated, or test-only.
3. If the phase text is stale, preserve the phase intent but implement against current canonical code.
4. Record the correction in the phase evidence report.
5. If the user corrects the canonical source, accept it as a workflow correction and update the governing project skill/reference.

## Payment-specific example

- Stale instruction: “mirror `/payment_new`”.
- Current reality: `/payment_new` redirects to `/payment`; `/payment` is canonical.
- Correct implementation: build `/payment_card` as a rail variant of `/payment`: same auth/query/product-selection behavior, different provider endpoint/copy/status path.

This avoids resurrecting deprecated code paths while still completing the SuperGoal phase.
