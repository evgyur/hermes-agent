# Bird Skill Setup

## 1. Install

Copy the `bird/` folder into your agent's skills directory, then reload/rescan skills if your agent requires it.

Expected structure:

```text
bird/
  SKILL.md
  bird.py
  SETUP.md
```

## 2. Configure SocialData

Register at https://socialdata.tools/ and create an API key.

Set it in the environment:

```bash
export SOCIALDATA_api key placeholder:"<your key>"
```

Or store it in a local config file:

```bash
mkdir -p ~/.config/bird
printf '%s' '<your key>' > ~/.config/bird/socialdata_api_key
chmod 600 ~/.config/bird/socialdata_api_key
```

Do not commit API keys, browser cookies, `.env` files, or local config files into the skill folder.

## 3. Optional dependency

The helper script uses only Python standard library modules.

## 4. Verify

```bash
cd bird
python3 -m py_compile bird.py
python3 bird.py --help
SOCIALDATA_api key placeholder:"<your key>" python3 bird.py fetch "https://x.com/i/status/<tweet_id>" --raw
```

## 5. What it supports

- Single tweet fetches.
- Thread fetches.
- X Article extraction from nested SocialData article payloads.
- User profile lookup.
- Search queries.
- Primary media download when media URLs are present in the tweet payload.
