"""ModerationEngine: spam + text rules + AI second opinion + fight detection → Verdict."""
from __future__ import annotations

import logging

from .. import ai as ai_mod
from .models import MsgInfo, Verdict, Violation
from .spam import ConversationTracker, SpamDetector
from .text_rules import Lexicon, TextAnalyzer

log = logging.getLogger("beerus.engine")


class ModerationEngine:
    def __init__(self, cfg, groq: ai_mod.GroqClient | None = None) -> None:
        self.cfg = cfg
        self.groq = groq
        self.lexicon = Lexicon(cfg.lexicon)
        self.text = TextAnalyzer(self.lexicon)
        self.spam = SpamDetector()
        self.tracker = ConversationTracker()

    def reload(self) -> None:
        self.lexicon = Lexicon(self.cfg.lexicon)
        self.text = TextAnalyzer(self.lexicon)

    async def evaluate(self, info: MsgInfo, privileged: bool = False) -> Verdict:
        cfg = self.cfg
        v = Verdict()
        self.tracker.add_context(info)

        # 1) spam family
        v.violations.extend(self.spam.check(info, cfg, privileged))

        # 2) language / abuse rules
        friendly = self.tracker.friendly_pair(info)
        tr = self.text.analyze(info, cfg, friendly)
        v.language, v.banter, v.score = tr.language, tr.banter, tr.score
        if tr.had_quote:
            v.notes.append("quoted text ignored")
        if tr.meta:
            v.notes.append("talking about a word")
        if tr.banter:
            v.notes.append("friendly banter")
        if tr.hits:
            v.notes.append("matched: " + tr.hit_summary())

        thr = int(cfg.get("moderation.warn_threshold", 55))
        low, high = (cfg.get("moderation.ai_review_range", [25, 55]) + [55])[:2]
        text_violation = False
        if tr.category and tr.score >= thr:
            v.violations.append(Violation(tr.category, tr.severity, tr.score, tr.hit_summary(), "rules"))
            text_violation = True
        elif tr.score >= low or (tr.ai_cue and len(info.content) >= 15):
            if not (cfg.get("moderation.ai_review_enabled", True) and self.groq):
                fb = int(cfg.get("moderation.local_fallback_threshold", 45))
                if tr.category and tr.score >= fb and not tr.banter:
                    v.violations.append(Violation(tr.category, tr.severity, tr.score, tr.hit_summary(), "rules"))
                    text_violation = True
                return self._finish(info, v, text_violation)
            ctx = self.tracker.recent_context(info, int(cfg.get("ai.classifier.context_messages", 5)))
            res = await ai_mod.classify(self.groq, cfg, info, ctx)
            if res is None:
                v.notes.append("AI unavailable")
                fb = int(cfg.get("moderation.local_fallback_threshold", 45))
                if tr.category and tr.score >= fb and not tr.banter:
                    v.violations.append(Violation(tr.category, tr.severity, tr.score, tr.hit_summary(), "rules"))
                    text_violation = True
            else:
                v.ai_used = True
                v.notes.append(f"AI: {res['category']} conf={res['confidence']:.2f} banter={res['banter']}")
                if res["violation"] and not res["banter"] \
                        and res["confidence"] >= float(cfg.get("ai.classifier.min_confidence", 0.7)):
                    sc = max(tr.score, thr)
                    v.violations.append(Violation(res["category"], res["severity"], sc, "AI context review", "ai"))
                    v.score = sc
                    text_violation = True
                elif res["banter"]:
                    v.banter = True

        return self._finish(info, v, text_violation)

    def _finish(self, info: MsgInfo, v: Verdict, text_violation: bool) -> Verdict:
        """Fight / escalation check (uses the toxicity score of this message)."""
        self.tracker.record(info, v.score)
        if not text_violation:
            fv = self.tracker.fight(info, self.cfg, v.score)
            if fv:
                v.violations.append(fv)
        return v

    def fight_notice_due(self, info: MsgInfo) -> bool:
        return self.tracker.should_notice(info, self.cfg)
