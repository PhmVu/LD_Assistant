from __future__ import annotations

import re


USERNAME_RE = re.compile(r"^[a-z0-9._-]+$")


def normalize_labeler_username(raw_username: str) -> tuple[str, str]:
    """Return (appen_username, display_name) for LD labeler accounts."""
    raw = (raw_username or "").strip().lower()
    raw = re.sub(r"\s+", "", raw)
    if not raw:
        raise ValueError("username is required")

    name = raw
    if name.startswith("jr-"):
        name = name[3:]
    if name.endswith("-ty"):
        name = name[:-3]
    name = name.strip("-")

    if not name or not USERNAME_RE.fullmatch(name):
        raise ValueError("username format is invalid")

    return f"jr-{name}-ty", name
