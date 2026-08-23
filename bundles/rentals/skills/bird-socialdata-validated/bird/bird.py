#!/usr/bin/env python3
"""Bird: X/Twitter fetch helper backed by SocialData.

Public-safe helper: credentials are read from environment variables or
~/.config/bird/socialdata_api_key. No credentials are stored in this file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

API_BASE = "https://api.socialdata.tools"


def get_key() -> str:
    """Return SocialData API key from env or local config file."""
    key = os.environ.get("SOCIALDATA_API_KEY") or os.environ.get("SOCIALDATA_KEY")
    if key:
        return key.strip()
    key_path = Path.home() / ".config" / "bird" / "socialdata_api_key"
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()
    raise RuntimeError(
        "SocialData API key not found. Set SOCIALDATA_API_KEY, SOCIALDATA_KEY, "
        "or ~/.config/bird/socialdata_api_key. Get a key at https://socialdata.tools/."
    )


def extract_tweet_id(source: str) -> Optional[str]:
    """Extract tweet/status ID from X/Twitter URL or return numeric input."""
    source = (source or "").strip().rstrip("/")
    patterns = [
        r"(?:https?://)?(?:www\.)?x\.com/[^/]+/status/(\d+)",
        r"(?:https?://)?(?:www\.)?twitter\.com/[^/]+/status/(\d+)",
        r"(?:https?://)?(?:www\.)?x\.com/i/status/(\d+)",
        r"(?:https?://)?(?:www\.)?x\.com/i/web/status/(\d+)",
        r"(?:https?://)?(?:www\.)?twitter\.com/i/web/status/(\d+)",
        # This is the article id, not always the tweet id. Kept as a last-resort numeric extractor.
        r"(?:https?://)?(?:www\.)?x\.com/i/article/(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    if source.isdigit():
        return source
    return None


def normalize_username(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://(?:www\.)?(?:x|twitter)\.com/", "", value)
    value = value.split("/", 1)[0]
    return value.lstrip("@")


def request_json(path: str, params: Optional[Dict[str, str]] = None, timeout: int = 30) -> Any:
    key = get_key()
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "bird-skill/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"SocialData API HTTP {e.code}: {body}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"SocialData returned invalid JSON: {e}") from e


def fetch_tweet(source: str) -> Dict[str, Any]:
    tweet_id = extract_tweet_id(source)
    if not tweet_id:
        raise ValueError(f"Cannot extract tweet ID from: {source}")
    return request_json(f"/twitter/tweets/{tweet_id}")


def fetch_thread(source: str) -> List[Dict[str, Any]]:
    tweet_id = extract_tweet_id(source)
    if not tweet_id:
        raise ValueError(f"Cannot extract tweet/conversation ID from: {source}")
    data = request_json(f"/twitter/thread/{tweet_id}")
    return data if isinstance(data, list) else [data]


def fetch_article(source: str) -> Dict[str, Any]:
    tweet_id = extract_tweet_id(source)
    if not tweet_id:
        raise ValueError(f"Cannot extract tweet/article ID from: {source}")
    return request_json(f"/twitter/article/{tweet_id}")


def fetch_user(username: str) -> Dict[str, Any]:
    return request_json(f"/twitter/user/{normalize_username(username)}")


def search(query: str) -> Dict[str, Any]:
    return request_json("/twitter/search", {"query": query})


def entities_urls(tweet: Dict[str, Any]) -> Iterable[str]:
    entities = tweet.get("entities") or {}
    for item in entities.get("urls") or []:
        if isinstance(item, dict):
            url = item.get("expanded_url") or item.get("url")
            if url:
                yield url


def is_article_tweet(tweet: Dict[str, Any]) -> bool:
    if tweet.get("article"):
        return True
    return any("x.com/i/article/" in url or "twitter.com/i/article/" in url for url in entities_urls(tweet))


def fetch_x_content(source: str) -> Dict[str, Any]:
    """Fetch and classify X content as article, thread, or tweet."""
    tweet = fetch_tweet(source)
    tweet_id = str(tweet.get("id_str") or extract_tweet_id(source) or "")

    if is_article_tweet(tweet):
        try:
            article_payload = fetch_article(tweet_id)
            return {"type": "article", "tweet": tweet, "article_payload": article_payload}
        except Exception as e:
            return {"type": "tweet", "tweet": tweet, "article_error": str(e)}

    conversation_id = str(tweet.get("conversation_id_str") or tweet.get("conversation_id") or tweet_id)
    if conversation_id and conversation_id != tweet_id:
        try:
            thread = fetch_thread(conversation_id)
            if len(thread) > 1:
                return {"type": "thread", "tweets": thread, "root_id": conversation_id}
        except Exception as e:
            return {"type": "tweet", "tweet": tweet, "thread_error": str(e)}

    # Root tweets can still have same-author follow-up posts.
    try:
        thread = fetch_thread(tweet_id)
        same_author = []
        root_user = ((tweet.get("user") or {}).get("id_str") or (tweet.get("user") or {}).get("id"))
        for item in thread:
            user = item.get("user") or {}
            if not root_user or user.get("id_str") == root_user or user.get("id") == root_user:
                same_author.append(item)
        if len(same_author) > 1:
            return {"type": "thread", "tweets": same_author, "root_id": tweet_id}
    except Exception:
        pass

    return {"type": "tweet", "tweet": tweet}


def tweet_text(tweet: Dict[str, Any]) -> str:
    return tweet.get("full_text") or tweet.get("text") or ""


def tweet_url(tweet: Dict[str, Any]) -> str:
    user = tweet.get("user") or {}
    screen_name = user.get("screen_name") or user.get("username") or "i"
    tweet_id = tweet.get("id_str") or tweet.get("id") or ""
    return f"https://x.com/{screen_name}/status/{tweet_id}"


def format_article(payload: Dict[str, Any]) -> str:
    article = payload.get("article_payload") or payload
    if article.get("article"):
        article = article["article"]
    title = article.get("title") or "X Article"
    preview = article.get("preview_text") or ""
    blocks = (article.get("content_state") or {}).get("blocks") or []
    lines = [f"📖 [article] {title}"]
    if preview:
        lines += ["", preview]
    for block in blocks:
        text = (block.get("text") or "").strip()
        if not text:
            continue
        btype = block.get("type") or "unstyled"
        if btype.startswith("header"):
            lines += ["", f"## {text}"]
        elif btype == "blockquote":
            lines.append(f"> {text}")
        elif btype == "unordered-list-item":
            lines.append(f"- {text}")
        elif btype == "ordered-list-item":
            lines.append(f"1. {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def format_tweet(tweet: Dict[str, Any]) -> str:
    user = tweet.get("user") or {}
    stats = {
        "likes": tweet.get("favorite_count", 0),
        "retweets": tweet.get("retweet_count", 0),
        "views": tweet.get("views_count", 0),
        "bookmarks": tweet.get("bookmark_count", 0),
    }
    return (
        f"🐦 [tweet] {tweet_text(tweet).splitlines()[0][:100] if tweet_text(tweet) else ''}\n"
        f"👤 {user.get('name','')} (@{user.get('screen_name','')}) — {user.get('followers_count',0):,} followers\n"
        f"📊 {stats['likes']:,}❤ {stats['retweets']:,}🔁 {stats['views']:,}👁 {stats['bookmarks']:,}🔖\n"
        f"📅 {tweet.get('tweet_created_at') or tweet.get('created_at') or ''}\n\n"
        f"{tweet_text(tweet)}\n\n"
        f"🔗 Source: {tweet_url(tweet)}"
    )


def format_thread(tweets: List[Dict[str, Any]], root_id: str = "") -> str:
    first = tweets[0] if tweets else {}
    user = first.get("user") or {}
    body = "\n\n".join(f"{i+1}. {tweet_text(t)}" for i, t in enumerate(tweets))
    root_url = tweet_url(first) if first else (f"https://x.com/i/status/{root_id}" if root_id else "")
    return (
        f"🧵 [thread] by @{user.get('screen_name','')}\n"
        f"👤 {user.get('name','')} — {user.get('followers_count',0):,} followers\n"
        f"📊 {len(tweets)} tweets\n\n{body}\n\n🔗 Source: {root_url}"
    )


def format_content(data: Dict[str, Any]) -> str:
    ctype = data.get("type")
    if ctype == "article":
        return format_article(data)
    if ctype == "thread":
        return format_thread(data.get("tweets") or [], data.get("root_id") or "")
    return format_tweet(data.get("tweet") or data)


def collect_media(tweet: Dict[str, Any]) -> List[Dict[str, Any]]:
    media: List[Dict[str, Any]] = []
    for key in ("entities", "extended_entities"):
        value = tweet.get(key) or {}
        if isinstance(value.get("media"), list):
            media.extend(value["media"])
    return media


def download_primary_media(source: str, output_dir: str) -> Optional[str]:
    tweet = fetch_tweet(source)
    media_items = collect_media(tweet)
    if not media_items:
        return None
    chosen_url = None
    suffix = ".bin"
    for media in media_items:
        variants = ((media.get("video_info") or {}).get("variants") or [])
        mp4 = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
        if mp4:
            best = max(mp4, key=lambda v: int(v.get("bitrate") or 0))
            chosen_url = best["url"]
            suffix = ".mp4"
            break
    if not chosen_url:
        for media in media_items:
            if media.get("type") == "photo" and (media.get("media_url_https") or media.get("media_url")):
                chosen_url = media.get("media_url_https") or media.get("media_url")
                suffix = ".jpg"
                break
    if not chosen_url:
        return None
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"bird_media_{extract_tweet_id(source) or 'download'}{suffix}"
    req = urllib.request.Request(chosen_url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://x.com/"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        out.write_bytes(resp.read())
    return str(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch X/Twitter content via SocialData")
    sub = parser.add_subparsers(dest="command")

    p_fetch = sub.add_parser("fetch", help="Fetch URL or tweet ID and auto-detect tweet/thread/article")
    p_fetch.add_argument("source")
    p_fetch.add_argument("--raw", action="store_true")

    p_thread = sub.add_parser("thread", help="Fetch a thread/conversation by ID or URL")
    p_thread.add_argument("source")
    p_thread.add_argument("--raw", action="store_true")

    p_article = sub.add_parser("article", help="Fetch X Article by containing tweet ID/URL")
    p_article.add_argument("source")
    p_article.add_argument("--raw", action="store_true")

    p_user = sub.add_parser("user", help="Fetch X user profile")
    p_user.add_argument("username")
    p_user.add_argument("--raw", action="store_true")

    p_search = sub.add_parser("search", help="Search X/Twitter via SocialData")
    p_search.add_argument("query")
    p_search.add_argument("--raw", action="store_true")

    p_media = sub.add_parser("media", help="Download primary media from a tweet")
    p_media.add_argument("source")
    p_media.add_argument("output_dir", nargs="?", default="/tmp")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    try:
        if args.command == "fetch":
            data = fetch_x_content(args.source)
            print(json.dumps(data, ensure_ascii=False, indent=2) if args.raw else format_content(data))
        elif args.command == "thread":
            data = fetch_thread(args.source)
            print(json.dumps(data, ensure_ascii=False, indent=2) if args.raw else format_thread(data, extract_tweet_id(args.source) or ""))
        elif args.command == "article":
            data = fetch_article(args.source)
            print(json.dumps(data, ensure_ascii=False, indent=2) if args.raw else format_article(data))
        elif args.command == "user":
            data = fetch_user(args.username)
            if args.raw:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"👤 {data.get('name','')} (@{data.get('screen_name') or data.get('username','')}) — {data.get('followers_count',0):,} followers\n{data.get('description') or ''}")
        elif args.command == "search":
            data = search(args.query)
            if args.raw:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                tweets = data.get("tweets") if isinstance(data, dict) else None
                if not tweets and isinstance(data, list):
                    tweets = data
                tweets = tweets or []
                print(f"🔎 {len(tweets)} results")
                for i, tweet in enumerate(tweets[:10], 1):
                    print(f"\n{i}. {tweet_text(tweet)[:280]}\n   {tweet_url(tweet)}")
        elif args.command == "media":
            path = download_primary_media(args.source, args.output_dir)
            if not path:
                print("No media found", file=sys.stderr)
                return 2
            print(path)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
