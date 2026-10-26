"""Rule-based text analysis: profanity, slurs, threats, bullying, sexual content, hate, bait.

Understands Roman Urdu / Hindi / Punjabi / English, creative spellings, censored words
(b*hen, m@), spaced letters, quoted text, banter and "talking about a word".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import MsgInfo
from .normalize import Token, clean_text, detect_language, squeeze, strip_quotes, tokenize

TIER_W = {"slur": 85, "hate_term": 60, "hard": 65, "strong": 40, "soft": 20, "ambiguous": 15}
TIER_RANK = {"ambiguous": 1, "soft": 2, "strong": 3, "hate_term": 4, "hard": 5, "slur": 6}
RACISM_TERMS = {"nigger", "nigga", "niggah", "chink", "chinki", "paki", "kalu", "kallu", "kaalu", "kalia",
                "habshi", "spic", "kike", "bhangi", "chuhra", "chura", "chamar", "katua", "katwa"}
LAUGH = {"lol", "lmao", "lmfao", "xd", "jk", "mazak", "mazaak", "mazaaq", "joke", "haha", "hehe"}
LAUGH_EMOJI = set("😂🤣😭💀😹😆😜😝")
LAUGH_SQ = {squeeze(x) for x in LAUGH}
BRIDGE = {"ki", "ka", "ke", "ko", "di", "da", "de", "nu", "ne", "kay"}
FAMILY_VERBS = {"chod", "chodu", "chodna", "chodunga", "chud", "chudai", "chudwa", "choda", "pel", "thok", "thokna"}
CAT_PRIORITY = ["threat", "racism", "hate_speech", "sexual_harassment", "bullying", "personal_attack",
                "nsfw", "profanity", "provocation"]


@dataclass
class Hit:
    tier: str
    term: str
    idx: int
    via: str  # exact | prefix | fuzzy | masked | joined


def within_one_edit(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if a == b:
        return True
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) > len(b):
            i += 1
        elif len(a) < len(b):
            j += 1
        else:
            i += 1
            j += 1
    edits += (len(a) - i) + (len(b) - j)
    return edits <= 1


class Lexicon:
    def __init__(self, data: dict) -> None:
        def norm(w: str) -> str:
            return squeeze(re.sub(r"[^a-z]", "", w.lower()))

        self.exact: dict[str, str] = {}
        self.mask_words: list[tuple[str, str]] = []
        tiers = [("ambiguous", "ambiguous"), ("soft", "soft"), ("strong", "strong"),
                 ("hate_terms", "hate_term"), ("hard", "hard"), ("slurs", "slur")]
        for key, tier in tiers:
            for w in data.get(key, []):
                n = norm(w)
                if n:
                    self.exact[n] = tier
                plain = re.sub(r"[^a-z]", "", w.lower())
                if len(plain) >= 4 and tier in ("hard", "strong", "slur", "hate_term"):
                    self.mask_words.append((plain, tier))
        self.prefix_words = [w for w, t in self.exact.items() if t == "hard" and len(w) >= 6]
        self.fuzzy_words = [w for w, t in self.exact.items() if t == "hard" and len(w) >= 8]
        self.sexual = {norm(w) for w in data.get("sexual", [])}
        self.groups = {norm(w) for w in data.get("groups", [])}
        self.group_insults = {norm(w) for w in data.get("group_insults", [])}
        self.family = {norm(w) for w in data.get("family_words", [])}
        self.second = {re.sub(r"[^a-z]", "", w.lower()) for w in data.get("second_person", [])}
        self.first = {re.sub(r"[^a-z]", "", w.lower()) for w in data.get("first_person", [])}
        self.ai_cues = {w.lower() for w in data.get("ai_cues", [])}
        self.banter = [str(w).lower() for w in data.get("banter_markers", [])]
        self.meta = [str(w).lower() for w in data.get("meta_markers", [])]
        pats = data.get("patterns", {})
        self.patterns = {k: [re.compile(p, re.I) for p in v] for k, v in pats.items()}

    # ── token matching ───────────────────────────────────────
    def _match_form(self, form: str) -> tuple[str, str, str] | None:
        cands = [form]
        if len(form) > 3 and form.endswith("s"):
            cands.append(form[:-1])
        if len(form) > 4 and form.endswith("es"):
            cands.append(form[:-2])
        for c in cands:
            tier = self.exact.get(c)
            if tier:
                return tier, c, "exact"
        if len(form) >= 6:
            for pw in self.prefix_words:
                if form.startswith(pw):
                    return "hard", pw, "prefix"
        if len(form) >= 8:
            for fw in self.fuzzy_words:
                if within_one_edit(form, fw):
                    return "hard", fw, "fuzzy"
        return None

    def find(self, tokens: list[Token]) -> list[Hit]:
        hits: list[Hit] = []
        used: set[int] = set()
        for i, t in enumerate(tokens):
            best: tuple[str, str, str] | None = None
            for form in t.forms:
                m = self._match_form(form)
                if m and (best is None or TIER_RANK[m[0]] > TIER_RANK[best[0]]):
                    best = m
            if best is None and t.wildcard is not None:
                for word, tier in self.mask_words:
                    if t.wildcard.fullmatch(word):
                        cand = (tier, word, "masked")
                        if best is None or TIER_RANK[tier] > TIER_RANK[best[0]]:
                            best = cand
            if best:
                hits.append(Hit(best[0], best[1], i, best[2]))
                used.add(i)
        # "bhen chod", "madar chod", "mother fucker"
        for i in range(len(tokens) - 1):
            for n in (2, 3):
                span = tokens[i:i + n]
                if len(span) < n or any(not s.forms for s in span):
                    continue
                if any(j in used for j in range(i, i + n)):
                    continue
                joined = "".join(s.main for s in span)
                if len(joined) < 7:
                    continue
                tier = self.exact.get(joined)
                if tier in ("hard", "slur", "strong"):
                    hits.append(Hit(tier, joined, i, "joined"))
                    used.update(range(i, i + n))
                    break
        return hits


@dataclass
class Candidate:
    category: str
    severity: int
    weight: float
    protected: bool
    tag: str = ""


@dataclass
class TextResult:
    score: int = 0
    language: str = "unknown"
    banter: bool = False
    meta: bool = False
    targeted: bool = False
    had_quote: bool = False
    ai_cue: bool = False
    candidates: list[Candidate] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    category: str | None = None
    severity: int = 0

    def hit_summary(self) -> str:
        """Masked terms for the (private) mod log, e.g. 'ch****a'."""
        out = []
        for h in self.hits[:5]:
            w = h.term
            out.append(f"{w[0]}{'•' * max(1, len(w) - 2)}{w[-1]}" if len(w) > 2 else w)
        return ", ".join(out)


class TextAnalyzer:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lex = lexicon

    @staticmethod
    def _combine(ws: list[float]) -> float:
        ws = sorted(ws, reverse=True)
        return sum(w * (0.5 ** i) for i, w in enumerate(ws))

    def _family(self, tokens: list[Token], hit_idx: set[int]) -> bool:
        for i, t in enumerate(tokens):
            if not any(f in self.lex.family for f in t.forms):
                continue
            window = tokens[i + 1:i + 5]
            before = tokens[max(0, i - 3):i]
            has_second = any(b.plain in self.lex.second for b in before)
            has_bridge = any(w.main in BRIDGE for w in tokens[i + 1:i + 3])
            has_abuse = (any(j in hit_idx for j in range(i + 1, i + 5))
                         or any(w.mask_only or w.censored for w in window)
                         or any(w.main in FAMILY_VERBS for w in window))
            if has_abuse and (has_second or has_bridge):
                return True
        return False

    def _group_hate(self, tokens: list[Token], hit_idx: set[int]) -> bool:
        for i, t in enumerate(tokens):
            if t.main not in self.lex.groups:
                continue
            for j in range(max(0, i - 3), min(len(tokens), i + 4)):
                if j == i:
                    continue
                if tokens[j].main in self.lex.group_insults or j in hit_idx:
                    return True
        return False

    def analyze(self, info: MsgInfo, cfg, friendly_pair: bool = False) -> TextResult:
        res = TextResult()
        body, res.had_quote = strip_quotes(info.content)
        clean = clean_text(body)
        tokens = tokenize(clean)
        res.language = detect_language(tokens).label
        if not tokens:
            return res

        plain = {t.plain for t in tokens if t.plain}
        res.targeted = bool(plain & self.lex.second) or info.mention_count > 0 \
            or (info.reply_to_author_id is not None and info.reply_to_author_id != info.author_id)
        about_me = bool(plain & self.lex.first) and not (plain & self.lex.second)

        hits = self.lex.find(tokens)
        res.hits = hits
        hit_idx = {h.idx for h in hits}
        cands: list[Candidate] = []
        dampen_hard = bool(cfg.get("moderation.banter.dampen_hard_words", False))

        # ── lexicon hits ────────────────────────────────────
        for h in hits:
            w = TIER_W[h.tier]
            if h.tier == "slur":
                cat = "racism" if h.term in RACISM_TERMS else "hate_speech"
                cands.append(Candidate(cat, 3, w, True, h.term))
            elif h.tier == "hate_term":
                if res.targeted:
                    cands.append(Candidate("hate_speech", 2, w, True, h.term))
            elif h.tier == "hard":
                cat = "personal_attack" if res.targeted else "profanity"
                cands.append(Candidate(cat, 2, w, not dampen_hard, h.term))
            elif h.tier == "strong":
                if res.targeted:
                    cands.append(Candidate("personal_attack", 2, w, False, h.term))
                elif not about_me:
                    cands.append(Candidate("profanity", 1, w * 0.5, False, h.term))
            elif h.tier in ("soft", "ambiguous"):
                if res.targeted and not about_me:
                    cands.append(Candidate("personal_attack", 1, w, False, h.term))

        # ── composite rules ─────────────────────────────────
        if self._family(tokens, hit_idx):
            cands.append(Candidate("personal_attack", 3, 70, True, "family"))
        if self._group_hate(tokens, hit_idx):
            cands.append(Candidate("hate_speech", 2, 60, True, "group"))

        sexual_n = sum(1 for t in tokens if t.main in self.lex.sexual)
        if sexual_n:
            w = 35 + 20 * (sexual_n - 1)
            cands.append(Candidate("sexual_harassment" if res.targeted else "nsfw", 2, w, False, "sexual"))

        pats = self.lex.patterns
        def any_match(key: str) -> bool:
            return any(p.search(clean) for p in pats.get(key, []))

        if any_match("threat"):
            cands.append(Candidate("threat", 3, 75, True, "threat"))
        if any_match("threat_soft"):
            cands.append(Candidate("threat", 2, 45 if res.targeted else 25, False, "threat_soft"))
        if any_match("bully_severe"):
            cands.append(Candidate("bullying", 3, 80, True, "bully_severe"))
        if any_match("bully"):
            cands.append(Candidate("bullying", 2, 35, False, "bully"))
        if any_match("bait"):
            cands.append(Candidate("provocation", 1, 35, False, "bait"))
        if any_match("sexual_harass"):
            cands.append(Candidate("sexual_harassment", 3, 75, True, "sexual_harass"))
        if any_match("hate_generic"):
            cands.append(Candidate("hate_speech", 2, 65, True, "hate_generic"))

        res.candidates = cands

        # ── intent / context signals ────────────────────────
        pad = f" {clean} "
        laugh = sum(1 for t in tokens if squeeze(t.plain) in LAUGH_SQ
                    or re.fullmatch(r"(?:ha|he){2,}h?", t.plain or "")) \
            + sum(1 for c in info.content if c in LAUGH_EMOJI)
        friendly = sum(1 for t in tokens if t.plain in {"yaar", "yar", "yrr", "bro", "bhai", "dost", "buddy", "bhaijaan"})
        res.banter = bool(cfg.get("moderation.banter.enabled", True)) and (
            laugh >= 1 or friendly >= 2 or friendly_pair)
        res.meta = any(f" {m} " in pad for m in self.lex.meta)

        if not cands:
            res.ai_cue = bool(res.targeted and (plain & self.lex.ai_cues) and len(clean) >= 15)
            return res

        prot = [c.weight for c in cands if c.protected]
        damp = [c.weight for c in cands if not c.protected]
        p_score = self._combine(prot)
        d_score = self._combine(damp)
        if res.targeted and (prot or damp):
            d_score += 15
            if info.reply_to_is_bot:
                d_score += 5
        letters = [c for c in info.content if c.isalpha()]
        if len(letters) >= 6 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            d_score += 5
        if info.content.count("!") >= 3:
            d_score += 3

        if res.banter and not friendly_pair:
            d_score *= float(cfg.get("moderation.banter.dampen", 0.4))
        elif res.banter and friendly_pair:
            d_score *= float(cfg.get("moderation.banter.dampen", 0.4)) * 0.8
        total = p_score + d_score
        if res.meta:
            total *= 0.5 if res.targeted else 0.2
        res.score = int(min(100, round(total)))

        # winning category
        by_cat: dict[str, float] = {}
        sev: dict[str, int] = {}
        for c in cands:
            by_cat[c.category] = max(by_cat.get(c.category, 0), c.weight)
            sev[c.category] = max(sev.get(c.category, 0), c.severity)
        res.category = max(by_cat, key=lambda k: (by_cat[k], -CAT_PRIORITY.index(k) if k in CAT_PRIORITY else 0))
        res.severity = sev[res.category]
        return res
