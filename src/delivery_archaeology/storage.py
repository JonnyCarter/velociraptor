from __future__ import annotations

import hashlib
import re


def safe_cache_slug(value: str) -> str:
    text = value.strip()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-_")
    slug = slug[:80] or "value"
    if slug == text:
        return slug
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"
