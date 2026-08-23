# Validation

Generated from `/tmp/human20-june-workshop-skills-only` and Human20 live API evidence.

Checks run:

- all bundled ZIP archives open with `ZipFile.testzip() == None`;
- no `__pycache__`, `.pytest_cache`, `.env`, `auth.json`, or sqlite files are bundled;
- `git diff --check` is clean;
- `scripts/install-skills.sh` passes `bash -n`;
- `python3 -m compileall -q scripts skills metadata` passes after fixing two public-clean `video-use` placeholder lines into valid `api_key = load_api_key(provider)` assignments;
- token-shaped secret scan is clean (`SECRET_SCAN.txt`).

Known caveat:

- `create-skill` workflow guard does **not** pass across every imported third-party/workshop skill. This repo intentionally preserves workshop source artifacts rather than rewriting all skills to Hermes internal house style. See original archives + `metadata/skills_manifest.csv` for provenance.
