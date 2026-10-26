"""Offline self-test of the moderation rules. Run:  python tests/check_detection.py
Exits non-zero if any case is wrong (also runs in the GitHub workflow before the bot starts)."""
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from beerus.config import Config                      # noqa: E402
from beerus.moderation.engine import ModerationEngine  # noqa: E402
from beerus.moderation.models import MsgInfo           # noqa: E402

# (message, should_warn)
CASES = [
    ("hello bhai kya haal hai", False), ("assalam o alaikum sab ko", False), ("tusi bohat vadhiya ho paaji", False),
    ("tu madarchod hai", True), ("m@darch0d", True), ("b h e n c h o d", True), ("bhenchoooood", True),
    ("tu ek chutiya hai", True), ("ch*tiya saala", True), ("teri m@ ki ****", True), ("tu b*hen ki l***", True),
    ("bhen chod", True), ("tu m*d*rch*d hai", True), ("what the fuck is this", True), ("tusi kutte ho", True),
    ("tu kutta hai", True), ("you are a stupid idiot", True), ("hindu terrorists saale", True), ("kafir hai tu", True),
    ("I will kill you", True), ("maar dunga tujhe", True), ("kys loser", True), ("send nudes", True), ("nigger", True),
    ("teri maa ki aankh", False), ("yaar bc kya hua gaadi kharab ho gayi", False), ("tu pagal hai 😂", False),
    ("main pagal hoon yaar", False), ("this game is trash lol", False), ("tu gaali mat de yaar", False),
    ("this is a bigger problem", False), ("class assignment due", False), ("> madarchod\nye kisne bola?", False),
]


def main() -> int:
    eng = ModerationEngine(Config(), None)
    bad = 0
    for i, (text, expected) in enumerate(CASES):
        info = MsgInfo(1, 1, i, 1000 + i, f"u{i}", text, time.time() + i * 60)
        v = asyncio.run(eng.evaluate(info))
        got = any(x.category not in ("flood", "duplicate", "fight") for x in v.violations)
        if got != expected:
            bad += 1
            print(f"❌ {text!r}: expected {'WARN' if expected else 'OK'}, got score={v.score} {[x.category for x in v.violations]}")
    print(f"{'✅' if not bad else '❌'} {len(CASES) - bad}/{len(CASES)} detection cases passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
