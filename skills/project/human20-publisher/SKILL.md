---
name: human20-publisher
description: "Publishes Human20 lessons or meetings from a supplied video/link/path into beta first: asks required clarification questions, recovers media, generates the unified cartoon-style cover, transcript, timecodes, lesson steps, homework for lessons only, meeting payloads for meetings, access checks, beta smoke, and prod promotion only after explicit command. Use when Chip gives a Human20 video and wants it fully prepared for the site."
version: 1.0.0
---

# Human20 Publisher

Use this when Chip gives a Human20 video/link/path and wants a complete site-ready publication package.

It supports two modes:

- `lesson` — урок / онбординг / воркшоп-урок. Includes homework.
- `meeting` — встреча / созвон / запись встречи. No homework by default.

Default deployment rule: **beta first only**. Prod happens only after Chip explicitly says to move it to prod.

## Trigger

Use this skill for requests like:

- «вот ссылка на урок, оформи полностью»
- «выложи следующую встречу»
- «сделай как онбординг Codex + Hermes»
- «сделай мультяшную обложку и транскрибацию»
- «возьми видео и добавь в Человек 2.0»
- «Human20-publisher»

## Load first

Depending on source and target, load:

- `project/human20-app` — repo/site contracts.
- `project/human20-prod-verification` — beta/prod smoke, release gates, entitlements.
- `chip-url-first` + `telegram-chip` — Telegram links/replies or URLs.
- `chip-visuals` only for routing context; then use this skill's cartoon cover workflow for lesson/meeting thumbnails.
- `chip-tubescribe`, `tubescribe`, `timecodes`, `whisper`, `turboscribe`, or `local-ocr` when transcription/extraction path needs it.

## Mandatory intake questions

Before editing the site, ask only the missing questions that affect the artifact. If the answer is obvious from the request/source, infer it and continue.

Minimum decision questions:

1. **Тип публикации:** это `урок` или `встреча`?
2. **Название:** как назвать материал на сайте?
3. **Текст на обложке снизу:** exact title for the green lower-third band.
4. **Куда поставить:** новый воркшоп/старый воркшоп/встречи/другая секция?
5. **Доступ:** кто должен видеть полный материал — участники воркшопа, все участники Среда внедрения ИИ, публично, или конкретный entitlement?
6. **Видео:** есть ли готовый HLS/файл/Telegram media, или нужно только подготовить shell + transcript + cover?
7. **Для урока:** нужна ли домашка? Если да, цель домашки и обязательные ссылки/сервисы.
8. **Для встречи:** нужно ли добавить упомянутые скилы/репозитории/материалы как attachments/links?
9. **Прод:** всегда `нет` на старте; prod only после отдельной команды.

If Chip says «сделай сам» or gives only a video link, default:

- infer `lesson` vs `meeting` from wording/source title;
- create a practical working title;
- propose lower-third title from transcript;
- paid/workshop-gated unless the source is clearly public;
- beta only.

## Output Contract

A complete beta delivery reports:

1. mode: `lesson` or `meeting`;
2. source evidence: URL/message/path, duration, media metadata;
3. site routes and public asset paths;
4. cover path and visual QA verdict;
5. transcript status, bytes/lines, normalization checks;
6. timecodes/chapters status;
7. lesson-only homework status;
8. access/entitlement behavior;
9. tests/lint/build/deploy commands and results;
10. beta release id + commit SHA;
11. remaining blockers, if any;
12. explicit note: `prod не трогал` unless prod was separately requested and completed.

## Workflow

### 1) Resolve mode and source

1. Recover the exact source:
   - Telegram: fetch exact message/reply and download media if needed.
   - URL: extract/open the source, do not guess from preview text.
   - Local/remote path: verify existence, size, duration, codec.
2. Decide mode:
   - `lesson`: course route, onboarding, numbered урок, homework expected.
   - `meeting`: созвон, Q&A, interview, event recording, no homework.
3. Ask missing intake questions from the mandatory list.
4. Create a stable task dir:

```text
/tmp/human20-publisher-<slug>/
  source/
  transcript.raw.txt
  transcript.clean.txt
  chapters.json
  cover-draft.png
  cover-final.jpg
  smoke.md
```

### 2) Generate transcript and chapters

1. Transcribe with timestamps where possible.
2. Export clean TXT.
3. Normalize important names mechanically and sample manually:
   - `Codex`, `Hermes`, `Perplexity`, `GitHub`, `VPS`, `OpenClaw`, `Cursor`, `Человек 2.0`, `human20.app`.
4. Build timecodes from transcript.
5. Validate chapters against actual timestamps and content.

### 3) Unified cartoon cover

Read `references/cartoon-cover-workflow.md` before producing the cover.

Default cover style for both lessons and meetings:

- `1280x720` JPEG;
- best video frame / provided portrait as base;
- `50% cartoon / 50% photo` hybrid;
- real person remains recognizable;
- clean contour lines, painted shading, saturated highlights;
- exact Cyrillic lower-third title rendered locally;
- no private data in the image.

Never rely on image model text for final Cyrillic. Use local overlay.

### 4) Build lesson package

Only for `lesson` mode:

- add/update lesson card on `/lessons` or the relevant lesson section;
- add detail route `/content/<lesson-id>`;
- add cover/poster;
- add transcript TXT under `/files/<lesson-id>/transcript.txt` when allowed;
- write structured sections:
  - `Зачем этот урок`;
  - `Что будет внутри`;
  - `Что сделать после просмотра`;
  - `Главные выводы`;
  - `Урок по шагам` / `Подробный разбор`;
- add concrete homework with `id="homework"` anchor and bottom `↑ К домашнему заданию` link;
- avoid duplicate generic accordion labels.

### 5) Build meeting package

Only for `meeting` mode:

- no homework by default;
- build meeting metadata/payload according to current Human20 app schema;
- include summaries, chapters, mentioned skills/repos/materials when relevant;
- use encrypted HLS for private recordings when video is published;
- do not place raw private video or private full transcript under public files;
- export backend snapshot if the current app requires it for meetings/progress/favorites.

### 6) Beta implementation

Patch the current Human20 repo, preserving unrelated dirty work.

Common files depend on current app contracts, but typical paths include:

- `frontend-v2/src/app/lessons/page.tsx` for lesson placement;
- `frontend-v2/src/lib/repositories/human20.ts` for content records;
- `frontend-v2/src/lib/data/homework.ts` for lesson homework;
- `frontend-v2/public/files/<id>/transcript.txt` for allowed lesson transcripts;
- `frontend-v2/public/files/thumbnails/<id>.jpg` for covers;
- generated meeting payload/snapshot files for meeting mode.

Before beta deploy:

```bash
cd frontend-v2
npm test -- session access-policy public-video-player payment-core || true
npm run lint
npm run build
```

Then commit, push beta, deploy beta with the canonical project script, and verify `/release.json`.

### 7) Beta smoke

Required browser/live checks:

- target list page shows the new item in the right place;
- detail route loads;
- cover URL returns `200` and looks right visually;
- transcript/artifact URLs behave according to access policy;
- guest sees preview/CTA only for paid materials;
- entitled users are expected to see full content;
- dark theme has no unreadable cards;
- no duplicate headings or broken buttons.

### 8) Prod promotion

Prod is a separate command.

When Chip later says to move beta to prod:

1. use a fresh prod worktree from `origin/prod`;
2. selectively merge/copy only publisher-related files;
3. preserve prod-only guards and payment/video fixes;
4. run focused tests, lint, build, preflight, `git diff --check`;
5. push prod only after remote safety check;
6. deploy with canonical prod script;
7. verify live `/release.json`, routes, assets, and entitlement behavior.

## Quick Test Checklist

- [ ] `вот ссылка на урок, оформи полностью` triggers lesson mode and asks for missing title/cover/access questions before edits.
- [ ] `выложи встречу` triggers meeting mode and does not create homework.
- [ ] Cover workflow uses cartoon 16:9 style, not square Telegram `human20-cover` by default.
- [ ] Beta deploy is mandatory before any prod step.
- [ ] Prod is refused/deferred unless Chip explicitly asks for prod.
- [ ] Entitlement/access boundary is checked before reporting done.

## Manual Review Checklist

Before reporting a beta delivery as done:

- [ ] mode is correct: lesson vs meeting;
- [ ] title and lower-third cover text are exact;
- [ ] cover is 1280x720 and readable on mobile;
- [ ] transcript generated and normalized, or blocker stated;
- [ ] chapters/timecodes match transcript;
- [ ] lesson homework is concrete and not duplicated;
- [ ] meeting has no accidental homework;
- [ ] private video/transcript policy respected;
- [ ] beta release SHA matches pushed commit;
- [ ] guest/entitled access behavior checked;
- [ ] prod untouched unless explicitly commanded.

## Done Criteria

- Intake questions resolved or safe defaults recorded.
- Source evidence and media metadata recorded.
- Unified cartoon cover produced and QA'd.
- Transcript/timecodes produced and validated where source allows.
- Lesson or meeting payload implemented according to mode.
- Tests/lint/build passed or exact blockers reported.
- Beta deployed and smoked.
- Prod not touched without explicit command.
