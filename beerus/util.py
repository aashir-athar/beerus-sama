from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def trunc(text: str, n: int) -> str:
    text = (text or "").replace("```", "'''")
    return text if len(text) <= n else text[: max(0, n - 1)] + "…"


def age_text(created: datetime) -> str:
    """Roman-Punjabi account age: '2 saal, 3 mahine, 5 din'."""
    days = max(0, (utcnow() - created).days)
    if days == 0:
        return "aj hi banaya gaya!"
    y, rest = divmod(days, 365)
    m, d = divmod(rest, 30)
    parts = []
    if y:
        parts.append(f"{y} saal")
    if m:
        parts.append(f"{m} mahine")
    if d and not y:
        parts.append(f"{d} din")
    return ", ".join(parts) or f"{d} din"


def duration_text(seconds: float) -> str:
    seconds = int(max(0, seconds))
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    if d:
        return f"{d} din, {h} ghante"
    if h:
        return f"{h} ghante, {m} min"
    return f"{m} min" if m else f"{seconds} sec"
