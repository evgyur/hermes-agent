# Human20 Rentals Autopilot Power Pack

Private Human20 rentals autopilot powerpack: curated June/Codex+Hermes workshop skills for the Ready Agent / rentals installation contour.

## Scope

- Source: current June/Codex+Hermes Human20 workshop materials, not February `lesson-6`.
- Excluded: raw chats, transcripts, videos, HTML demos, old `workshop-5-6` catalog batch, and `meeting-2026-06-12-skills.zip`.
- Included: extracted skill directories plus original ZIP/MD archives and metadata.

## Install

```bash
bash scripts/install-skills.sh ~/.hermes/skills
```

## Bundled skills by lesson

### codex-github-start
- `deep-hermes-hel1-noscrets` — Deep
- `perplex-direct` — Perplex
- `perplex-hel1-20260622` — perplex-skill-hel1-20260622.zip
- `prompt-optimizer` — Prompt Optimizer
- `reasoning-personas` — Reasoning Personas
- `superpowerss` — Superpowers
### downloads
- `codex-plugin` — codex-plugin.zip
- `codex-tg` — codex-tg.zip
- `tg-chip` — tg-skill-chip.zip
- `vercel-labs-agents` — vercel-labs-agent-skills.zip
### jun-lesson-5
- `guardianangel` — Guardian Angel skill
- `summ_skill` — summary skill
- `tg-postcraft` — TG + Postcraft skills
### jun-lesson-6
- `bird-socialdata-validated` — bird + Social Data skill ZIP
- `chip-marketing` — chip-marketing skill ZIP
- `img-chip-cover` — img+chip-cover skill ZIP
- `restic-doctor` — restic-doctor skill ZIP
- `video-generation` — video generation skill public validated
- `video-use` — video use public validated
### lesson-2
- `design-taste-frontend` — design-taste-frontend.zip
- `hallmark` — hallmark
- `image-to-code` — image-to-code-skill.zip
- `refero-design` — refero_skill
- `refero-web-design` — refero-web-design
- `shaw` — shaw
- `taste` — taste-skill.zip
### lesson-3
- `chip-webb` — chip-webb — production web/VPS/Supabase skill
- `create` — create-skill — создание и правка навыков
- `seo-aeo-geo-principal` — seo-aeo-geo-principal — SEO/AEO/GEO аудит
- `webd` — webd.zip

## GitHub refs

See `metadata/github_skill_refs.csv`.

## Verification

- `metadata/skills_manifest.csv/json` maps source URL → archive → extracted skill path.
- `SHA256SUMS.txt` contains hashes for tracked non-git files.
- `SECRET_SCAN.txt` records token-shaped scan results.
