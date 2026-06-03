# Artifact retention during skill refactors

When refactoring a bloated skill that includes generated media or other artifacts, separate three classes before deleting anything.

## Protected

Keep these unless the user explicitly says otherwise:

- reusable templates and brand assets;
- renderers/scripts that generate current outputs;
- manifests used by a renderer or random/template pool;
- curated golden samples used by regression/QA;
- source assets that cannot be regenerated deterministically.

## Disposable

Usually safe to delete after a short rollback window:

- one-off generated outputs;
- old preview images;
- cache files under `~/.hermes/cache/`;
- backup copies of disposable generated outputs.

## Procedure

1. Inventory asset paths and render scripts.
2. Ask: can this file be regenerated from source/template? If yes, it is probably disposable.
3. Before cleanup, add a retention policy reference to the owning skill.
4. Delete by allowlist, not by broad directory removal.
5. Verify protected renderers still produce valid outputs after cleanup.

Session-derived pitfall: old `/tg gen` images were disposable, but Human20 cover templates and HLRU background pools were reusable protected assets.