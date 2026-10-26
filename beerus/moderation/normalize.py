"""Text cleaning, tokenising and language detection (English / Roman Urdu / Hindi / Punjabi)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff\u00ad]")
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
DISCORD_TAGS = re.compile(r"<(?:@[!&]?|#|a?:\w+:)\d*>|<a?:\w+:\d+>")
CODE_BLOCK = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]+`")
BLOCKQUOTE = re.compile(r"^\s*>{1,3}\s?.*$", re.M)
SPACED_LETTERS = re.compile(r"(?<![a-z0-9])(?:[a-z][ \t.\-_*,]){3,}[a-z](?![a-z0-9])")
TOKEN_RE = re.compile(r"[a-z0-9@$#*%!?_.\-+]+")
SQUEEZE_RE = re.compile(r"(.)\1+")
MD_WRAP = re.compile(r"(\*\*|__|~~)(.+?)\1")

LEET = str.maketrans({"@": "a", "$": "s", "0": "o", "1": "i", "3": "e", "4": "a",
                      "5": "s", "7": "t", "!": "i", "+": "t"})
MASK_CHARS = set("*#%?_")
LEET_CHARS = set("@$!+0123456789")


def squeeze(s: str) -> str:
    """bhenchoddd → bhenchod ; kutta → kuta (applied to lexicon too, so both sides agree)."""
    return SQUEEZE_RE.sub(r"\1", s)


def strip_quotes(text: str) -> tuple[str, bool]:
    """Remove Discord blockquotes / code — people quoting someone else are not the offender."""
    had = bool(BLOCKQUOTE.search(text) or CODE_BLOCK.search(text))
    text = CODE_BLOCK.sub(" ", text)
    text = BLOCKQUOTE.sub(" ", text)
    text = INLINE_CODE.sub(" ", text)
    return text, had


def clean_text(text: str) -> str:
    text = ZERO_WIDTH.sub("", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = URL_RE.sub(" ", text)
    text = DISCORD_TAGS.sub(" ", text)
    text = text.replace("||", " ")
    text = MD_WRAP.sub(r"\2", text)           # **bold** / __under__ / ~~strike~~ are formatting, not censoring
    # join "b h e n c h o d" / "b.h.e.n" → "bhenchod"
    text = SPACED_LETTERS.sub(lambda m: re.sub(r"[ \t.\-_*,]", "", m.group(0)), text)
    return text


@dataclass
class Token:
    raw: str                                   # as typed (cleaned)
    forms: list[str] = field(default_factory=list)   # squeezed candidate spellings
    wildcard: re.Pattern | None = None         # for censored words (b*hen)
    mask_only: bool = False                    # "****"
    censored: bool = False                     # "l***" — letters hidden behind mask characters
    plain: str = ""                            # letters-only, unsqueezed

    @property
    def main(self) -> str:
        return self.forms[0] if self.forms else ""


def _wildcard_regex(raw: str) -> re.Pattern | None:
    out: list[str] = []
    literal = 0
    i = 0
    while i < len(raw):
        c = raw[i]
        if c in MASK_CHARS:
            while i < len(raw) and raw[i] in MASK_CHARS:
                i += 1
            out.append(".{1,3}")
            continue
        if c in LEET_CHARS:
            out.append(".")
        elif c.isalpha():
            out.append(re.escape(c))
            literal += 1
        i += 1
    if literal < 2:
        return None
    return re.compile("".join(out))


def tokenize(clean: str) -> list[Token]:
    tokens: list[Token] = []
    for raw0 in TOKEN_RE.findall(clean):
        raw = raw0.strip(".-_!?+*#%")           # sentence punctuation / markdown at the edges
        if not raw:
            if sum(c in MASK_CHARS for c in raw0) >= 2:
                tokens.append(Token(raw=raw0, mask_only=True))
            continue
        edge_masks = sum(c in MASK_CHARS for c in raw0) - sum(c in MASK_CHARS for c in raw)
        if all(c in MASK_CHARS or c in "@$" for c in raw) and len(raw) >= 2:
            tokens.append(Token(raw=raw, mask_only=True))
            continue
        if raw.isdigit():
            tokens.append(Token(raw=raw, forms=[], plain=""))
            continue
        forms: list[str] = []
        letters = re.sub(r"[^a-z]", "", raw)
        if letters:
            forms.append(squeeze(letters))
        if any(c in LEET_CHARS for c in raw) and len(letters) >= 1 and len(raw) >= 2:
            leeted = re.sub(r"[^a-z]", "", raw.translate(LEET))
            if leeted:
                sq = squeeze(leeted)
                if sq not in forms:
                    forms.append(sq)
        wildcard = None
        if any(c in MASK_CHARS for c in raw) or (any(c in "@$" for c in raw) and len(letters) >= 2):
            wildcard = _wildcard_regex(raw)
        censored = edge_masks >= 2 or (edge_masks >= 1 and len(letters) <= 2)
        tokens.append(Token(raw=raw, forms=forms, wildcard=wildcard, plain=letters, censored=censored))
    return tokens


# ─────────────────────────── language detection ───────────────────────────
def _s(words: str) -> frozenset[str]:
    return frozenset(words.split())


EN = _s("the is are you to and of in it that this what why how not but for with have just like good please "
        "thanks thank hello hi yes no ok okay we they he she was can will do dont im ive its your my me "
        "so very are when where who which because about really going come game play stop wait guys nice "
        "bro man lol")
SHARED = _s("hai hain nahi nahin kya ko ka ki ke se mein main mai tum tu hum yaar bhai kuch kyun kyu aur bhi "
            "toh to tha thi the raha rahi rahe kar karo kro ho hoga abhi ab ye yeh woh wo mera meri mere tera "
            "tere teri uska apna acha achha theek thik bas lekin par magar tumhe tujhe mujhe kaise kese")
UR = _s("bohat bohot shukriya mohabbat zaroor khuda inshallah mashallah janab waqai kyunke zyada bilkul ghalat "
        "aap ap hamesha alhamdulillah jazakallah dil ishq zindagi pasand khushi mubarak kesay kaisay kia kiya "
        "jee sahab shayad aajkal acha sahi hogaya hogayi wese")
HI = _s("bahut dhanyavaad pyaar pyar kripya bhagwan samay kyunki jaroor sab vaise isliye mat hoon hun "
        "bilkul accha thoda zyaada kahan kaha")
PA = _s("tusi tuhanu tuhada tuhadi sada sadi kiddan kithe kithon kiven ae ni nai oye hun kamm vekh jaanda "
        "karda karni ditta dasso mainu tainu tenu asi eh oh ik ikk haan ethe othe kujh kuj hauli gall gallan "
        "aaya puttar paaji veere vaar sanu dasna ki haal chaa kidaan kida kiwen kinna jinna menu koi nahin "
        "dasna dass kithey ohnu ehnu tuhade sade")


@dataclass
class LangResult:
    label: str          # english | roman_urdu | roman_hindi | roman_urdu_hindi | roman_punjabi | mixed | unknown
    scores: dict[str, int]


def detect_language(tokens: list[Token]) -> LangResult:
    sc = {"en": 0, "shared": 0, "ur": 0, "hi": 0, "pa": 0}
    for t in tokens:
        w = t.plain
        if not w:
            continue
        if w in PA and w not in ("hun", "eh", "oh", "ik", "koi"):
            sc["pa"] += 1
        if w in UR:
            sc["ur"] += 1
        if w in HI:
            sc["hi"] += 1
        if w in SHARED:
            sc["shared"] += 1
        if w in EN:
            sc["en"] += 1
    roman = sc["ur"] + sc["hi"] + sc["pa"] + sc["shared"]
    if sc["en"] + roman < 2:
        return LangResult("english" if sc["en"] else "unknown", sc)
    if sc["en"] >= 2 and roman >= 2:
        return LangResult("mixed", sc)
    if roman == 0:
        return LangResult("english", sc)
    if sc["pa"] >= 2 and sc["pa"] >= max(sc["ur"], sc["hi"]):
        return LangResult("roman_punjabi", sc)
    if sc["ur"] > sc["hi"]:
        return LangResult("roman_urdu", sc)
    if sc["hi"] > sc["ur"]:
        return LangResult("roman_hindi", sc)
    return LangResult("roman_urdu_hindi", sc)
