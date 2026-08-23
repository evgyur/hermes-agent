---
name: bird
version: 1.0.0
description: Fetch and analyze public X/Twitter content through SocialData — tweets, threads, X Articles, users, and search. Use when the user shares an x.com/twitter.com link or asks to retrieve, inspect, summarize, or verify X content.
---

# Bird Skill 🐦

Use this skill to fetch and analyze X/Twitter content with SocialData as the primary data source instead of brittle scraping.

## Trigger

Use when the task includes:

- an `x.com` or `twitter.com` tweet/status/article URL;
- a tweet ID, X username, or X search query to inspect;
- requests like “fetch this tweet”, “summarize this thread”, “open this X article”, “check this handle”, “find tweets about …”.

## Prerequisites

Set a SocialData API key using one of:

```bash
export SOCIALDATA_api key placeholder:"<your key>"
# or
export SOCIALDATA_KEY="<your key>"
# or save the key at:
mkdir -p ~/.config/bird
printf '%s' '<your key>' > ~/.config/bird/socialdata_api_key
chmod 600 ~/.config/bird/socialdata_api_key
```

Get an API key from: https://socialdata.tools/

If no key is available, stop and ask the user/operator to configure one. Do not pretend X content was fetched from previews or snippets.

## Workflow

1. Identify the input type:
   - `x.com/{user}/status/{tweet_id}` or `twitter.com/.../status/{tweet_id}` → tweet; may be part of a thread.
   - `x.com/i/status/{tweet_id}` or `x.com/i/web/status/{tweet_id}` → tweet.
   - `x.com/i/article/{article_id}` or article link inside a tweet → X Article; SocialData article fetch usually needs the tweet ID that contains the article.
   - `@username` / username → user profile or recent user tweets.
   - free-text query → SocialData search.
2. Fetch the tweet first for URL inputs.
3. Check `conversation_id_str` to decide whether to fetch a thread.
4. Check `entities.urls[].expanded_url` for `x.com/i/article/`; if present, fetch article content.
5. For judgment/verdict tasks, inspect linked sources and media when relevant before giving an opinion.
6. Report what was actually fetched: tweet/thread/article/user/search, author, date, stats, and source URL.

## Commands

From the skill folder:

```bash
python3 bird.py fetch "https://x.com/user/status/123"        # formatted output
python3 bird.py fetch "https://x.com/user/status/123" --raw  # JSON
python3 bird.py thread 1234567890 --raw
python3 bird.py article 1234567890
python3 bird.py user nasa
python3 bird.py search 'from:nasa moon since:2025-01-01' --raw
python3 bird.py media "https://x.com/user/status/123" /tmp
```

## SocialData endpoints used

Base URL: `https://api.socialdata.tools`

- `GET /twitter/tweets/{tweet_id}` — tweet object with text, user, entities, stats, media.
- `GET /twitter/thread/{conversation_id}` — conversation/thread tweets.
- `GET /twitter/article/{tweet_id}` — X Article nested under `article` in the tweet response.
- `GET /twitter/user/{username}` — profile metadata.
- `GET /twitter/search?query={query}` — search results.

Use `Authorization: Bearer <key>` for every request.

## Important parsing notes

### Tweet vs thread

A status URL alone is not enough to know whether the content is a thread.

- Fetch the tweet.
- Read `conversation_id_str`.
- If the conversation ID differs from the tweet ID, fetch the conversation root/thread.
- If `/twitter/thread/{id}` returns multiple same-author tweets, treat it as a thread.

### X Articles

SocialData article responses are usually tweet-shaped objects with article data nested at `response["article"]`:

```python
article = response["article"]
title = article.get("title")
blocks = article.get("content_state", {}).get("blocks", [])
for block in blocks:
    text = block.get("text", "")
```

Block types may include `unstyled`, `header-two`, `header-three`, `blockquote`, `unordered-list-item`, and `ordered-list-item`.

### Search robustness

Large search responses can include unusual characters. If JSON parsing fails, retry with tolerant UTF-8 decoding or fetch specific tweet IDs from partial results.

## Output contract

For short fetches:

```text
📖 [tweet/thread/article/user/search] <title or first line>
👤 <name> (@<screen_name>) — <followers> followers
📊 <likes>❤ <retweets>🔁 <views>👁 <bookmarks>🔖
📅 <date>

<content>

🔗 Source: <url>
```

For investigations/verdicts:

- `evidence`: exact fetched object(s) and any linked/media sources inspected.
- `answer`: concise conclusion grounded in that evidence.
- `caveat`: missing credentials, API errors, deleted/private content, or inaccessible links.

## Error handling

| Situation | Action |
|---|---|
| Missing API key | Ask operator to set `SOCIALDATA_API_KEY` or `~/.config/bird/socialdata_api_key`. |
| 401/403 | Key invalid or unauthorized; do not fall back to fabricated previews. |
| 402 | Credits exhausted; say so. |
| 404 | Tweet/user/article not found or unavailable. |
| 429 | Rate limited; wait or retry later. |
| JSON parse failure | Retry tolerant decoding or fetch narrower objects. |

## Quick test checklist

- [ ] `python3 -m py_compile bird.py` passes.
- [ ] `python3 bird.py --help` prints usage without requiring credentials.
- [ ] `python3 bird.py fetch <tweet-url> --raw` returns JSON when a valid SocialData key is configured.
- [ ] Thread links are checked via `conversation_id_str` rather than guessed from the URL.
- [ ] X Article content is read from nested `article.content_state.blocks`.
- [ ] The skill stops honestly when credentials are missing or the API returns an error.

## Done criteria

- X content is fetched through SocialData or reported as unavailable with a specific API/status reason.
- The final answer distinguishes tweet, thread, article, user, and search evidence.
- No private credentials, local machine paths, chat IDs, owner names, or project-specific routing assumptions are embedded in the skill.
