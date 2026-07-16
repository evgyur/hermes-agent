"""Pure normalization helpers for Telegram mention wake-word regexes."""
from __future__ import annotations

import re
from typing import List


def normalize_mention_patterns(patterns: object) -> List[str]:
    """Split legacy ``(?i)a,(?i)b`` values without splitting literal commas."""
    if not isinstance(patterns, str):
        return []
    raw = patterns.strip()
    if not raw:
        return []
    return [
        part.strip()
        for part in re.split(r",(?=\(\?[aiLmsux-]+\))", raw)
        if part.strip()
    ]
