"""Plain data objects shared by the moderation modules (no Discord dependency → easy to test)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MsgInfo:
    guild_id: int
    channel_id: int
    message_id: int
    author_id: int
    author_name: str
    content: str
    ts: float
    mention_count: int = 0              # user mentions (any user, incl. the bot)
    role_mention_count: int = 0
    mentions_everyone: bool = False     # typed @everyone/@here (even if Discord blocked the ping)
    reply_to_author_id: int | None = None
    reply_to_is_bot: bool = False
    reply_to_content: str = ""
    attachment_count: int = 0
    account_age_days: float = 9999.0
    author_is_admin: bool = False
    mentions_bot: bool = False


@dataclass
class Violation:
    category: str
    severity: int          # 1 mild · 2 serious · 3 severe
    score: int
    detail: str = ""
    source: str = "rules"  # rules | spam | ai | fight


@dataclass
class Verdict:
    score: int = 0
    language: str = "unknown"
    banter: bool = False
    violations: list[Violation] = field(default_factory=list)
    needs_ai: bool = False
    ai_used: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def primary(self) -> Violation | None:
        if not self.violations:
            return None
        return max(self.violations, key=lambda v: (v.severity, v.score))
