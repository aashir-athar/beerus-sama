"""CI guard: fails if any project source contains a kick / ban / mute / timeout call.
Beerus-Sama is WARN-ONLY by design."""
import pathlib
import re
import sys

FORBIDDEN = [
    r"\.\s*(kick|ban|unban|timeout|bulk_ban)\s*\(",
    r"timed_out_until",
    r"\bcommunication_disabled_until\b",
    r"\bmoderate_members\b",
    r"\bkick_members\b",
    r"\bban_members\b",
    r"\bMember\.edit\(.*timed",
]
root = pathlib.Path(__file__).resolve().parent.parent
bad = []
for path in list(root.glob("beerus/**/*.py")) + [root / "main.py"]:
    text = path.read_text(encoding="utf-8")
    for pat in FORBIDDEN:
        for m in re.finditer(pat, text):
            line = text[: m.start()].count("\n") + 1
            bad.append(f"{path.relative_to(root)}:{line}: {m.group(0)}")
if bad:
    print("❌ Punishment code found (Beerus must only warn):")
    print("\n".join(bad))
    sys.exit(1)
print("✅ No kick / mute / timeout / ban code found. Warn-only rule holds.")
