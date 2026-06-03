#!/usr/bin/env python3
"""🐦 Bird - X/Twitter content fetcher utility
Usage: import bird; result = await bird.fetch_tweet(url_or_id)
"""
import os
import re
import json
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List

SOCIALDATA_KEY = None

def _get_key() -> str:
    """Lazy load API key"""
    global SOCIALDATA_KEY
    if SOCIALDATA_KEY is None:
        key_path = os.path.expanduser("$SOCIALDATA_KEY")
        try:
            with open(key_path) as f:
                SOCIALDATA_KEY = f.read().strip()
        except FileNotFoundError:
            raise RuntimeError(f"SocialData API key not found at {key_path}")
    return SOCIALDATA_KEY


def extract_tweet_id(url: str) -> Optional[str]:
    """Extract tweet ID from x.com or twitter.com URL."""
    # Normalize input (trim spaces, drop trailing slash)
    source = (url or "").strip().rstrip('/')

    patterns = [
        # Canonical tweet URLs
        r'x\.com/[^/]+/status/(\d+)',
        r'twitter\.com/[^/]+/status/(\d+)',

        # X internal status routes (commonly shared from app)
        r'x\.com/i/status/(\d+)',
        r'x\.com/i/web/status/(\d+)',
        r'twitter\.com/i/web/status/(\d+)',

        # Article route (kept for backward compatibility)
        r'x\.com/i/article/(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1)

    # If just a number, assume it's a tweet ID
    if source.isdigit():
        return source
    return None


async def fetch_tweet(tweet_id: str, timeout: int = 30) -> Dict[str, Any]:
    """Fetch single tweet by ID using SocialData API"""
    import aiohttp
    
    key = _get_key()
    url = f"https://api.socialdata.tools/twitter/tweets/{tweet_id}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status == 404:
                raise ValueError(f"Tweet {tweet_id} not found")
            if resp.status == 402:
                raise RuntimeError("SocialData credits exhausted")
            if resp.status != 200:
                raise RuntimeError(f"SocialData API error: {resp.status}")
            
            return await resp.json()


async def fetch_thread(conversation_id: str, timeout: int = 30) -> list:
    """Fetch thread by conversation ID"""
    import aiohttp
    
    key = _get_key()
    url = f"https://api.socialdata.tools/twitter/thread/{conversation_id}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"SocialData API error: {resp.status}")
            
            data = await resp.json()
            return data if isinstance(data, list) else [data]


def _collect_tweet_media(tweet: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect media objects from tweet payload, including common nested locations."""
    media = []

    entities = tweet.get('entities') or {}
    if isinstance(entities.get('media'), list):
        media.extend(entities['media'])

    extended_entities = tweet.get('extended_entities') or {}
    if isinstance(extended_entities.get('media'), list):
        media.extend(extended_entities['media'])

    # Some payloads may nest media inside retweets / quoted tweets
    for nested_key in ('retweeted_status', 'quoted_status'):
        nested = tweet.get(nested_key)
        if isinstance(nested, dict):
            nested_entities = nested.get('entities') or {}
            if isinstance(nested_entities.get('media'), list):
                media.extend(nested_entities['media'])
            nested_extended = nested.get('extended_entities') or {}
            if isinstance(nested_extended.get('media'), list):
                media.extend(nested_extended['media'])

    # Deduplicate by media_key / id_str / url
    deduped = []
    seen = set()
    for item in media:
        key = item.get('media_key') or item.get('id_str') or item.get('url')
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


async def download_primary_media(source: str, output_dir: str = "/tmp") -> Optional[str]:
    """Download the best primary media asset from a tweet.

    Preference:
    1. Highest bitrate MP4 video
    2. Animated GIF MP4
    3. First photo
    Returns local file path or None.
    """
    import aiohttp

    tweet_id = extract_tweet_id(source)
    if not tweet_id:
        raise ValueError(f"Cannot extract tweet ID from: {source}")

    tweet = await fetch_tweet(tweet_id)
    media_items = _collect_tweet_media(tweet)
    if not media_items:
        return None

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    chosen_url = None
    chosen_path = None
    chosen_kind = None

    for media in media_items:
        media_type = media.get('type')
        video_info = media.get('video_info') or {}
        variants = video_info.get('variants') or []
        mp4_variants = [v for v in variants if v.get('content_type') == 'video/mp4' and v.get('url')]
        if media_type in ('video', 'animated_gif') and mp4_variants:
            best = max(mp4_variants, key=lambda v: v.get('bitrate', 0))
            chosen_url = best['url']
            chosen_kind = 'video'
            chosen_path = output / 'tg_post_video.mp4'
            break

    if chosen_url is None:
        for media in media_items:
            media_type = media.get('type')
            photo_url = media.get('media_url_https') or media.get('media_url')
            if media_type == 'photo' and photo_url:
                chosen_url = photo_url
                chosen_kind = 'photo'
                chosen_path = output / 'tg_post_img.jpg'
                break

    if chosen_url is None or chosen_path is None:
        return None

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(chosen_url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to download tweet media: HTTP {resp.status}")
            data = await resp.read()

    with open(chosen_path, 'wb') as f:
        f.write(data)

    return str(chosen_path)


async def fetch_x_content(source: str) -> Dict[str, Any]:
    """Main entry point - auto-detect and fetch X content
    
    Args:
        source: x.com URL, twitter.com URL, or tweet ID
        
    Returns:
        Dict with: type, author, content, stats, url
    """
    tweet_id = extract_tweet_id(source)
    if not tweet_id:
        raise ValueError(f"Cannot extract tweet ID from: {source}")
    
    # Fetch tweet first
    tweet = await fetch_tweet(tweet_id)
    
    # Check if it's part of a thread
    conversation_id = tweet.get('conversation_id_str', tweet_id)
    
    # If conversation_id != tweet_id, it's part of a thread
    if conversation_id != tweet_id:
        try:
            thread = await fetch_thread(conversation_id)
            # Check if multiple tweets from same author = actual thread
            if len(thread) > 1:
                return {
                    'type': 'thread',
                    'author': thread[0]['user'],
                    'tweets': thread,
                    'count': len(thread),
                    'url': f"https://x.com/{thread[0]['user']['screen_name']}/status/{conversation_id}"
                }
        except:
            pass  # Fall back to single tweet
    
    # Single tweet
    return {
        'type': 'tweet',
        'author': tweet['user'],
        'content': tweet.get('full_text') or tweet.get('text', ''),
        'stats': {
            'likes': tweet.get('favorite_count', 0),
            'retweets': tweet.get('retweet_count', 0),
            'views': tweet.get('views_count', 0),
            'bookmarks': tweet.get('bookmark_count', 0),
        },
        'created_at': tweet.get('tweet_created_at', tweet.get('created_at', '')),
        'url': f"https://x.com/{tweet['user']['screen_name']}/status/{tweet_id}"
    }


def format_x_content(data: Dict[str, Any]) -> str:
    """Format fetched content for display"""
    if data['type'] == 'thread':
        tweets_text = '\n\n'.join([
            f"{i+1}. {t.get('full_text', t.get('text', ''))}"
            for i, t in enumerate(data['tweets'])
        ])
        return f"""🧵 **Thread** by @{data['author']['screen_name']}
👤 {data['author']['name']} ({data['author']['followers_count']:,} followers)
📊 {data['count']} tweets

{tweets_text}

🔗 {data['url']}"""
    
    else:  # tweet
        stats = data['stats']
        return f"""🐦 **Tweet** by @{data['author']['screen_name']}
👤 {data['author']['name']} ({data['author']['followers_count']:,} followers)
📊 {stats['likes']:,}❤  {stats['retweets']:,}🔁  {stats['views']:,}👁

{data['content']}

🔗 {data['url']}"""


# Synchronous wrapper for easy use
def get(source: str) -> Dict[str, Any]:
    """Synchronous wrapper - fetch X content"""
    return asyncio.run(fetch_x_content(source))


def get_formatted(source: str) -> str:
    """Synchronous wrapper - fetch and format"""
    data = get(source)
    return format_x_content(data)


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if not args:
        print("Usage: python bird.py <x.com/url> [--raw] [--download-primary-media [output_dir]]")
        raise SystemExit(1)

    source = None
    raw = False
    download_media = False
    output_dir = "/tmp"

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--raw':
            raw = True
            i += 1
            continue
        if arg == '--download-primary-media':
            download_media = True
            if i + 1 < len(args) and not args[i + 1].startswith('--'):
                output_dir = args[i + 1]
                i += 2
            else:
                i += 1
            continue
        if source is None:
            source = arg
        i += 1

    if not source:
        print("Usage: python bird.py <x.com/url> [--raw] [--download-primary-media [output_dir]]")
        raise SystemExit(1)

    if download_media:
        path = asyncio.run(download_primary_media(source, output_dir=output_dir))
        if path:
            print(path)
            raise SystemExit(0)
        raise SystemExit(2)

    data = get(source)
    if raw:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_x_content(data))
