---
name: tg
description: Use when preparing Telegram post previews from a topic, draft, source link, screenshot, or notes; handles source recovery, Telegram-safe formatting, preview/publish safety, and revision flow without private account-specific routing.
metadata:
  hermes:
    tags: [telegram, writing, preview, publishing, social]
    related_skills: [postcraft]
---

# tg — Telegram post adapter

`tg` is a lightweight adapter for turning source material or a draft into a Telegram-ready post. It is not the editorial core: use `postcraft` for writing, rewriting, tone, de-slop, and source preservation; use `tg` for Telegram packaging, preview flow, publish safety, and revisions.

## Trigger

Use this skill when the user asks to:

- make a Telegram post from a link, note, screenshot, video, transcript, or rough idea;
- rewrite an existing Telegram draft or preview;
- prepare a post with Telegram formatting, links, media caption, or inline button;
- review whether a Telegram post is ready to publish;
- publish or schedule a Telegram post, if the runtime has an approved send path.

Do not use this skill for long articles, landing pages, generic strategy, or pure copywriting without Telegram-specific constraints. Use `postcraft` directly for those.

## Safety contract

1. **Preview before public publish.** If the target is a public channel or group, prepare a preview first unless the user explicitly asks for immediate publishing.
2. **Publishing requires explicit target.** Do not publish to a public channel unless the user names the target and clearly approves publishing.
3. **Do not delete or edit live posts blindly.** For edits/deletes, fetch or identify the exact post/message first.
4. **No credential leakage.** Never include API keys, tokens, private chat IDs, personal IDs, internal paths, or private source dumps in a public post.
5. **Source-backed claims need source preservation.** For factual/news/finance/security/legal-ish posts, recover the source first and preserve exact names, numbers, links, and uncertainty.
6. **Verify delivery when tools allow it.** If the agent sends a preview/publish through Telegram tools, fetch back or inspect the send response before saying it is done.

## Workflow

### 1. Classify the lane

**Fast lane** — low-risk local rewrite or post from the user's own notes.

- use `postcraft` fast lane;
- keep the post short and Telegram-readable;
- no invented facts.

**Strict lane** — source/news/tweet/research-derived post, finance/security/legal-ish claims, public channel publish, or user complained about factual/style quality.

- recover source first;
- create a compact source brief;
- create editorial intent: reader, desired shift, preserve items, risk flags;
- draft through `postcraft` strict lane;
- run final Telegram readability pass.

**Recovery lane** — user says the preview is wrong, stale, missing, too long, or not using the latest draft.

- recover the latest actual draft/source from visible context;
- do not rewrite from an older cache or memory;
- preserve scope unless the user asks to shorten;
- send a fresh preview or return the corrected Telegram-ready copy.

### 2. Build Telegram-ready copy

Default Telegram shape:

- strong first line / hook;
- short paragraphs;
- useful facts before commentary;
- link entities or clean URLs;
- no overlong caption if sending under media;
- no hidden assumptions about what Telegram will render.

If returning copy for manual paste, avoid literal Markdown that may show as raw characters in captions. Use plain text unless the actual send path supports and verifies Markdown/HTML entities.

### 3. Media caption rule

Telegram media captions have stricter length and formatting behavior than normal text posts.

If a post is too long for a media caption:

- either shorten deliberately;
- or send media first and full text as a separate message;
- do not silently truncate.

### 4. Inline button rule

If the user asks for a button:

- include exact button text and URL in the preview/spec;
- verify the send path supports `reply_markup.inline_keyboard` before promising a visual button;
- if only returning manual copy, provide the button label and URL separately.

## Revision rules

When the user asks to change a preview:

1. Treat the newest user-visible draft/correction as source of truth.
2. Apply only the requested change unless the draft is structurally broken.
3. Preserve links, source facts, media, and CTA unless asked otherwise.
4. Return/send a new preview; do not merely explain the change.

## Output contract

If tools are available and a preview/publish was sent, report briefly:

```text
preview_sent: yes/no
chat_or_target: <target name if non-private>
message_id: <id if available>
media: yes/no
links/buttons: verified/not verified
blocker: <only if blocked>
```

If no Telegram send tool is available, return:

1. final Telegram-ready post;
2. optional button label + URL;
3. notes about source uncertainty or formatting limits.

## Quick test checklist

- [ ] Latest source/draft was used.
- [ ] Public publish was not done without explicit approval and target.
- [ ] Factual claims preserve source names/numbers/links.
- [ ] Post is readable on Telegram mobile.
- [ ] Media caption length/formatting was considered.
- [ ] Links/buttons are explicit and not hidden in private context.

## Done criteria

- The user has either a Telegram-ready post or a verified preview/publish result.
- No private identifiers, credentials, internal paths, or personal workflow details are included.
- `postcraft` remains the editorial layer; `tg` only handles Telegram-specific adaptation and safety.
