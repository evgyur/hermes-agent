# Public repo distribution for chip-img

Use when the user asks to push/publish the image skill.

## Canonical repo

- Public canonical repo: `evgyur/chip-img`.
- Do not confuse it with older `evgyur/img`; name similarity caused a wrong push once.
- If the canonical repo does not exist, create it as public only after preparing a clean tree and scanning for private material.

## Preserve fallback while modernizing

The skill has two useful paths:

1. Primary: GPT-Image-2 via Codex OAuth, including real `input_image` for reference-dependent work.
2. Fallback: FAL/Nano Banana runtime for non-critical text-to-image/edit work when Codex is unavailable.

When syncing from the live skill to a repo, preserve fallback files if they exist:

- `img_tool/`
- `scripts/img`
- `requirements.txt`

Do not remove them just because the Codex path became the primary workflow. Fallback is valuable when Codex auth/quota/backend fails.

## Fallback boundary

Use FAL/Nano Banana fallback only for:

- pure text-to-image;
- style exploration;
- non-critical edits;
- cases where `FAL_KEY` is configured and the user accepts fallback behavior.

Do not silently use fallback for:

- “1 в 1 лицо”;
- identity preservation;
- exact reference-image matching;
- poster/edit tasks where the user explicitly cares that the same person/object remains unchanged.

For those, fallback can produce a generic lookalike and should be treated as a quality regression unless the user explicitly accepts the tradeoff.

## Public safety scan

Before pushing public:

- scan for OAuth/API tokens (`ghp_`, `gho_`, `sk-`, etc.);
- scan for Telegram private chat IDs (`-100...`);
- scan for private absolute paths and local workspace paths;
- remove local auth files, caches, generated media, and private sync markers;
- verify `git diff --check` and compile/check scripts when applicable;
- after push, verify `gh repo view evgyur/chip-img` reports `visibility: PUBLIC` and local/remote HEAD match.
