# Canonical governance and rollout

## Source of truth

`human20team/human20-meeting-operations` is the only editable source for the Human20 meeting lifecycle. `main` is release-ready. Consumers pin an exact commit; they never edit vendored copies first.

The skill name remains `live-meeting-operations` for compatibility. Provider plugins, legacy aliases, transcript helpers, Calendar skills, and Team20 skills are adapters called by this root.

## Required release artifacts

Each canonical release contains:

- `SKILL.md` with semantic `version`;
- `VERSION` with the same value;
- `CHANGELOG.md` entry;
- `scripts/validate_contract.py` passing;
- all directly linked references/scripts;
- no secrets, tokens, raw transcripts, private chat exports, browser profiles, cookies, or runtime recordings.

## Downstream receipt

Every consuming bot/distribution records:

```json
{
  "schema": 1,
  "name": "live-meeting-operations",
  "source_repo": "human20team/human20-meeting-operations",
  "source_commit": "<40 hex>",
  "source_tree": "<40 hex>",
  "version": "<semver>",
  "entries": [{"path": "...", "sha256": "..."}]
}
```

Health is `PASS` only when the installed tree matches the receipt and the receipt commit is reachable from the approved canonical branch. A newer upstream commit is `UPDATE_AVAILABLE`, not silent failure. A changed installed tree is `DRIFT` and must fail closed for release claims.

## Rollout order

1. Change canonical repository.
2. Run contract validator, syntax checks, skill workflow guard, realistic trigger/negative tests, and secret/private-data scan.
3. Commit and push canonical `main`; record commit/tree SHA.
4. Materialize that exact archive into `human20bot-managed-skills`; write source receipt and rebuild its manifest.
5. Materialize the same exact archive into `human20team/hermes-agent-powerpack`; write source receipt and run skill/discovery tests.
6. Deploy through each bot's managed release mechanism. Never edit read-only live release directories.
7. Read back installed receipt/tree and run a no-side-effect readiness smoke.

## Change policy

- **Patch:** wording, provider pitfall, verification strengthening with no interface change.
- **Minor:** new provider/webinar/capture capability, new scripts/references, backward compatible.
- **Major:** changed privacy boundary, completion contract, skill name, output schema, or removal of a supported route.

Provider API/UI drift belongs in adapters/references where possible. Cross-provider policy, context/privacy boundaries, completion semantics, and Human20 handoff stay in the root.

## No parallel lifecycles

A downstream skill may say “load `live-meeting-operations`” and preserve an old command name, but it must not:

- create or join a second room;
- start a competing recorder or Realtime session;
- ask for a passcode before canonical API lookup;
- publish its own weaker `ready/done` state;
- hold an independent copy of shared policy;
- inject owner-private context into a shared Human20 room.

## Rollback

Rollback means pinning the previous known-good canonical commit and rebuilding downstream receipts/manifests. Do not hand-edit a deployed snapshot. Re-run no-side-effect checks and disclose any meeting already created under the reverted release.
