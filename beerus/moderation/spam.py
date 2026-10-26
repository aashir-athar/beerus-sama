"""Spam family: duplicate, flood, emoji, mention, caps, links, invites, scam links + fight tracker."""
from __future__ import annotations

import re
from collections import defaultdict, deque
from urllib.parse import urlparse

from .models import MsgInfo, Violation
from .normalize import squeeze

URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>()]+"
    r"|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:xyz|tk|ml|ga|cf|gq|top|click|link|zip|mov|ru|cn|biz|online|site|"
    r"store|shop|live|cc|ws|ly|gg|me|co|io|com|net|org|info|to)(?:/[^\s<>()]*)?\b",
    re.I,
)
INVITE_RE = re.compile(
    r"(?:discord(?:app)?\.com/invite|discord\.gg|dsc\.gg|discord\.me|discord\.io|invite\.gg|discord\.link)"
    r"/+([A-Za-z0-9-]+)", re.I)
CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
UNICODE_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U00002B00-\U00002BFF\U0000FE0F]")

SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "cutt.ly", "is.gd", "rb.gy", "shorturl.at", "tiny.cc",
              "ow.ly", "buff.ly", "rebrand.ly", "t.ly", "goo.gl", "v.gd"}
BAD_TLDS = {"xyz", "tk", "ml", "ga", "cf", "gq", "top", "click", "link", "zip", "mov", "ru", "cn", "biz",
            "online", "site", "store", "shop", "live", "cc", "ws"}
OFFICIAL_BRANDS = {"discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media",
                   "steampowered.com", "steamcommunity.com", "store.steampowered.com", "steamstatic.com"}
BRAND_LOOKALIKE = re.compile(r"(d[il1]sc[o0]rd|d[il1]sc0rd|discrod|steam|nitro|gift|airdrop|claim)", re.I)
SCAM_PHRASES = [
    r"free\s+nitro", r"nitro\s+(gift|giveaway|for\s+free)", r"steam\s+gift", r"claim\s+(your|ur)\s+(gift|prize|reward)",
    r"airdrop", r"verify\s+(your\s+)?wallet", r"wallet\s*connect", r"double\s+your\s+(money|crypto|btc)",
    r"earn\s+\$?\d+\s*(usd|dollars|\$)?\s*(daily|per\s+day|a\s+day)", r"you\s+(have\s+been|were)\s+selected",
    r"click\s+here\s+to\s+(verify|claim)", r"whatsapp\s+(group|me).{0,30}(earn|profit|invest)",
    r"investment\s+(plan|opportunity)", r"guaranteed\s+(profit|returns?)", r"crypto\s+(giveaway|signals?)",
    r"easypaisa.{0,20}(win|inam|prize)", r"jazzcash.{0,20}(win|inam|prize)", r"inam\s+(jeeta|jita|mila)",
]
SCAM_RE = [re.compile(p, re.I) for p in SCAM_PHRASES]


def extract_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;:!?") for m in URL_RE.finditer(text)]


def host_of(url: str) -> str:
    u = url if re.match(r"https?://", url, re.I) else "http://" + url
    try:
        host = (urlparse(u).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def domain_allowed(host: str, allowed: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in allowed)


class SpamDetector:
    def __init__(self) -> None:
        self.recent: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=40))      # (guild,user) → (ts, key)
        self.links: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=40))        # (guild,user) → ts per link
        self.times: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=40))        # (guild,user) → ts
        self.cross: dict[tuple[int, str], deque] = defaultdict(lambda: deque(maxlen=20))        # (guild,key) → (ts, user)

    # ──────────────────────────────────────────────────────────
    def check(self, info: MsgInfo, cfg, is_privileged: bool = False) -> list[Violation]:
        out: list[Violation] = []
        g, u, now = info.guild_id, info.author_id, info.ts
        k = (g, u)
        text = info.content

        # flood
        win = float(cfg.get("spam.flood.window_seconds", 8))
        mx = int(cfg.get("spam.flood.max_messages", 6))
        dq = self.times[k]
        dq.append(now)
        n = sum(1 for t in dq if now - t <= win)
        if n >= mx:
            out.append(Violation("flood", 2, 60, f"{n} messages in {win:.0f}s", "spam"))

        # duplicates (same user, and same text from many users)
        key = squeeze(re.sub(r"[^a-z0-9]", "", text.lower()))
        if len(key) >= 3 or (not key and info.attachment_count):
            dwin = float(cfg.get("spam.duplicate.window_seconds", 60))
            drep = int(cfg.get("spam.duplicate.max_repeats", 3))
            rd = self.recent[k]
            rd.append((now, key))
            same = sum(1 for t, kk in rd if kk == key and now - t <= dwin)
            if same >= drep:
                out.append(Violation("duplicate", 1, 55, f"same message x{same}", "spam"))
            cu = int(cfg.get("spam.duplicate.cross_user_min_users", 3))
            cd = self.cross[(g, key)]
            cd.append((now, u))
            users = {uu for t, uu in cd if now - t <= 30}
            if len(key) >= 8 and len(users) >= cu:
                out.append(Violation("duplicate", 2, 65, f"same text from {len(users)} users", "spam"))

        # emoji
        emo = len(CUSTOM_EMOJI.findall(text)) + len(UNICODE_EMOJI.findall(text))
        if emo > int(cfg.get("spam.emoji.max_per_message", 10)):
            out.append(Violation("emoji_spam", 1, 55, f"{emo} emojis", "spam"))

        # mentions
        total_m = info.mention_count + info.role_mention_count
        if total_m > int(cfg.get("spam.mentions.max_per_message", 4)):
            out.append(Violation("mention_spam", 2, 65, f"{total_m} mentions", "spam"))
        elif info.mentions_everyone and not is_privileged:
            out.append(Violation("mention_spam", 2, 60, "@everyone/@here attempt", "spam"))

        # caps
        letters = [c for c in re.sub(r"<[^>]+>|https?://\S+", "", text) if c.isalpha()]
        if len(letters) >= int(cfg.get("spam.caps.min_letters", 12)):
            ratio = sum(c.isupper() for c in letters) / len(letters)
            if ratio >= float(cfg.get("spam.caps.max_ratio", 0.7)):
                out.append(Violation("caps_spam", 1, 55, f"{ratio:.0%} caps", "spam"))

        # links
        urls = extract_urls(text)
        if urls:
            out.extend(self._links(info, cfg, urls, k, now))
        return out

    # ──────────────────────────────────────────────────────────
    def _links(self, info: MsgInfo, cfg, urls: list[str], k, now: float) -> list[Violation]:
        out: list[Violation] = []
        allowed = list(cfg.get("spam.links.allowed_domains", []) or [])
        ok_codes = {c.lower() for c in (cfg.get("spam.invites.allowed_codes", []) or [])}

        # invites
        for m in INVITE_RE.finditer(info.content):
            if m.group(1).lower() not in ok_codes:
                out.append(Violation("invite_link", 2, 70, "discord invite", "spam"))
                break

        # scam / suspicious
        risk = 0
        reasons: list[str] = []
        for url in urls:
            host = host_of(url)
            if not host:
                continue
            tld = host.rsplit(".", 1)[-1]
            if host in SHORTENERS:
                risk += 2
                reasons.append("shortener")
            if tld in BAD_TLDS and not domain_allowed(host, allowed):
                risk += 2
                reasons.append(f".{tld}")
            if host.startswith("xn--") or ".xn--" in host:
                risk += 3
                reasons.append("punycode")
            if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
                risk += 3
                reasons.append("ip-host")
            if "@" in url.split("//", 1)[-1].split("/", 1)[0]:
                risk += 2
                reasons.append("@-in-url")
            if BRAND_LOOKALIKE.search(host) and not domain_allowed(host, list(OFFICIAL_BRANDS)):
                risk += 3
                reasons.append("lookalike")
        phrase = sum(1 for r in SCAM_RE if r.search(info.content))
        if phrase:
            risk += 2 * phrase
            reasons.append("scam wording")
        if risk >= 3:
            out.append(Violation("scam_link", 3, 85, ", ".join(sorted(set(reasons))), "spam"))

        # link spam
        non_allowed = [u for u in urls if not domain_allowed(host_of(u), allowed)
                       and not INVITE_RE.search(u)]
        if len(non_allowed) > int(cfg.get("spam.links.max_per_message", 3)):
            out.append(Violation("link_spam", 1, 60, f"{len(non_allowed)} links", "spam"))
        lq = self.links[k]
        for _ in non_allowed:
            lq.append(now)
        lwin = float(cfg.get("spam.links.window_seconds", 60))
        in_win = sum(1 for t in lq if now - t <= lwin)
        if in_win > int(cfg.get("spam.links.max_in_window", 5)):
            out.append(Violation("link_spam", 1, 60, f"{in_win} links in {lwin:.0f}s", "spam"))
        return out


class ConversationTracker:
    """Per-channel heat: detects fights/escalation, and friendly back-and-forth (banter)."""

    def __init__(self) -> None:
        self.events: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=60))  # (guild,channel) → (ts,user,score,target)
        self.last_notice: dict[tuple[int, int], float] = {}
        self.context: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=12))  # (ts, name, text)

    def add_context(self, info: MsgInfo) -> None:
        self.context[(info.guild_id, info.channel_id)].append((info.ts, info.author_name, info.content[:200]))

    def recent_context(self, info: MsgInfo, n: int) -> list[tuple[str, str]]:
        items = list(self.context[(info.guild_id, info.channel_id)])[-(n + 1):-1]
        return [(name, text) for _, name, text in items]

    def friendly_pair(self, info: MsgInfo) -> bool:
        """Two people who've been chatting calmly (low scores) for the last couple of minutes."""
        if info.reply_to_author_id is None:
            return False
        key = (info.guild_id, info.channel_id)
        pair = {info.author_id, info.reply_to_author_id}
        rel = [e for e in self.events[key] if info.ts - e[0] <= 150 and e[1] in pair]
        if len(rel) < 3 or len({e[1] for e in rel}) < 2:
            return False
        return sum(e[2] for e in rel) / len(rel) < 15

    def record(self, info: MsgInfo, score: int) -> None:
        self.events[(info.guild_id, info.channel_id)].append((info.ts, info.author_id, score, info.reply_to_author_id))

    def fight(self, info: MsgInfo, cfg, score: int) -> Violation | None:
        w = float(cfg.get("moderation.fight.window_seconds", 90))
        heated = float(cfg.get("moderation.fight.heated_score", 30))
        key = (info.guild_id, info.channel_id)
        rel = [e for e in self.events[key] if info.ts - e[0] <= w and e[2] >= heated]
        if score < heated:
            return None
        users = {e[1] for e in rel}
        if len(users) >= int(cfg.get("moderation.fight.min_users", 2)) \
                and len(rel) >= int(cfg.get("moderation.fight.min_heated_messages", 4)):
            return Violation("fight", 2, 60, f"{len(users)} users, {len(rel)} heated msgs", "fight")
        return None

    def should_notice(self, info: MsgInfo, cfg) -> bool:
        key = (info.guild_id, info.channel_id)
        cd = float(cfg.get("moderation.fight.notice_cooldown_seconds", 120))
        if info.ts - self.last_notice.get(key, 0) >= cd:
            self.last_notice[key] = info.ts
            return True
        return False
