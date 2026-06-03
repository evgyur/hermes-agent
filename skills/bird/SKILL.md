---
name: bird
description: "Fetch and analyze X/Twitter tweets, threads, articles, users, and search results. Use when the user shares an x.com/twitter.com link, asks for X content extraction, or needs primary-source escalation from social claims."
metadata:
  hermes:
    tags: [x, twitter, socialdata, tweets, threads, articles]
---

# Bird

X/Twitter content fetch and analysis workflow. This packaged version is portable: no cookies, API keys, private secret paths, or account-specific data are included.

## Trigger
Use for:
- `x.com` / `twitter.com` links;
- tweet/thread/article extraction;
- X search, profile, replies, quotes, and context checks;
- social claim verification where the primary source may be on X.

## Credential model
Read `references/x-provider-safety.md` before any credentialed X task.

Use credentials only from runtime environment or configured secret manager:
- `SOCIALDATA_KEY` / `SOCIALDATA_API_KEY` for SocialData API;
- optional X/Twitter cookies only from the runtime's approved secret store.

Never print cookie values, bearer tokens, cookie field names, or API keys. If no credential is available, stop and say what is missing instead of scraping blocked X HTML.

## URL/type detection
- `x.com/{user}/status/{id}` or `twitter.com/{user}/status/{id}` → tweet; may be thread after fetch.
- `x.com/i/status/{id}` / `x.com/i/web/status/{id}` → tweet/internal share route.
- `x.com/i/article/{id}` or tweet with article expanded URL → X article; fetch article body if API supports it.

Tweet vs thread cannot be trusted from URL alone. Fetch first, then inspect conversation/root and same-author replies.

## Method priority
1. SocialData API or configured X data provider.
2. Cookie-authenticated X API if allowed by runtime policy, slow and non-parallel.
3. Browser/relay only as last resort for content visible in the authenticated session.

## Article parsing rule
Some APIs return X article data nested inside the tweet object. Look for `article.title`, `article.preview_text`, and `article.content_state.blocks[]`; do not assume a flat article object.

## Context enrichment
For "is this interesting?" or verdict requests:
1. Fetch tweet/thread/article.
2. Open linked repo/docs/article/product pages directly.
3. Analyze attached media/screenshots when present.
4. Base verdict on the combined package, not tweet text alone.

## Output Contract
Return:
1. content type and author;
2. full extracted text or concise faithful summary, depending on ask;
3. engagement/context when available;
4. source URL;
5. any missing credential/source limitation.

## Quick Test Checklist
- [ ] Missing credentials stop early with a clear blocker.
- [ ] Thread detection uses fetched conversation data, not URL guesswork.
- [ ] Article parsing handles nested `article.content_state.blocks`.
- [ ] Secrets/cookies are never printed.

## Done Criteria
- [ ] The user gets extracted X content or an honest credential/source blocker.
- [ ] Primary-source claims are not replaced by aggregator overclaims.
- [ ] No API keys, cookies, private paths, or account-specific data are bundled.
