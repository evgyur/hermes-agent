# Username OSINT with Maigret + source triangulation

Use this when a user asks to identify a person/account from a username, Telegram profile screenshot, handle, phone/ID clue, or asks to search “all sources”.

## Principle

A handle match is not an identity match. Treat Maigret results as candidate accounts, not one person, unless there are bridging facts: same real name, avatar, bio, linked URLs, phone, Telegram ID, email hash, or mutual cross-links.

## Minimal workflow

1. Extract anchors from the source artifact:
   - username/handle
   - real name / display name
   - Telegram id / channel username
   - phone/email if present, but do not print sensitive values back unless necessary
   - org/project names and dates

2. Run Maigret on the handle:

```bash
python3 -m venv /tmp/maigret-venv
/tmp/maigret-venv/bin/pip install -q --upgrade pip
/tmp/maigret-venv/bin/pip install -q maigret
mkdir -p /tmp/maigret-<handle>
/tmp/maigret-venv/bin/maigret <handle> --top-sites 500 --timeout 8 --retries 1 -n 64 \
  --no-progressbar --no-color -J simple -C -fo /tmp/maigret-<handle>
```

If `pycairo` build fails, install the system dependency then retry:

```bash
sudo apt-get update -qq
sudo apt-get install -y -qq libcairo2-dev pkg-config
```

For a broader scan:

```bash
mkdir -p /tmp/maigret-<handle>-all
/tmp/maigret-venv/bin/maigret <handle> -a --timeout 5 --retries 0 -n 128 \
  --no-recursion --no-extracting --no-progressbar --no-color -J simple -C \
  -fo /tmp/maigret-<handle>-all
```

3. Summarize Maigret output with collision control:

```python
import csv, json
rows = list(csv.DictReader(open('/tmp/maigret-<handle>-all/report_<handle>.csv', newline='', errors='ignore')))
claimed = [r for r in rows if r.get('exists') == 'Claimed']
unknown = [r for r in rows if r.get('exists') == 'Unknown']
print({'checked': len(rows), 'claimed': len(claimed), 'unknown': len(unknown)})
```

4. Triangulate with targeted searches:
   - Perplexity/Sonar exact queries: `"real name" handle`, `"@handle" "real name"`, `"project" "handle"`, exact phone/ID when appropriate.
   - GitHub API: `/users/<handle>` and search users by real name/project.
   - X/SocialData if relevant: search handle/name/project; fetch user profile only when handle exists.
   - Telegram via `telegram-chip-hermes` when the clue is Telegram: resolve username and search local message history through the API; never copy sessions.

## Reporting pattern

Return an executive OSINT summary with evidence tiers:

- HIGH confidence: direct platform resolution or exact real-name match.
- MEDIUM confidence: same handle plus matching project/channel/bio but no direct cross-link.
- LOW confidence: handle-only accounts or common-name hits.
- Reject / likely collision: same handle but conflicting names, countries, avatars, bios, or account theme.

Always include:
- what was checked and counts (`Maigret: N checked, M claimed`)
- strongest verified matches
- weak/collision matches explicitly separated
- what did not match
- next verification asks (LinkedIn, work email, signed message, shared admin proof, portfolio, etc.)

## Pitfalls

- Maigret can return dozens of accounts for common handles; do not merge them into one identity.
- HTTP 200 does not always mean account claimed; rely on Maigret status and then inspect rich fields.
- Search models can return irrelevant same-word products/brands; discard results that do not mention the target anchors.
- Do not expose full phone numbers or private contact fields in the final unless the user already supplied them and it is necessary for the task.
