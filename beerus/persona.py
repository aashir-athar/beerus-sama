"""Beerus-Sama — every word he says lives here.

Warnings, banter, anger  → Roman Pakistani Punjabi (Lahori style)
Love / thanks / welcome-back → Roman Urdu
Roman Punjabi has no single official spelling, so ONE consistent scheme is used throughout:
  ae = hai · ni = nahi · tusi/tenu/tuhanu · kujh · hun · fer · ik · gall · kamm · ditta · painda · haan
Edit freely — everything is plain text.
"""
from __future__ import annotations

import random
import re
import time
from collections import deque

MOODS = ("calm", "annoyed", "furious")
MOOD_LABEL = {"calm": "Thanda 😌", "annoyed": "Chirhya hoya 😤", "furious": "Hakai mode 🔥"}
MOOD_COLOR = {"calm": 0x7B2FBE, "annoyed": 0xF59E0B, "furious": 0xDC2626}


class HeatMeter:
    """Server 'temper'. Rises on violations, cools down with time."""

    def __init__(self) -> None:
        self.value: dict[int, tuple[float, float]] = {}   # guild → (heat, ts)

    def _now(self, guild: int) -> float:
        h, ts = self.value.get(guild, (0.0, time.time()))
        return max(0.0, h - (time.time() - ts) * 0.05)    # −3 per minute

    def bump(self, guild: int, amount: float) -> None:
        self.value[guild] = (self._now(guild) + amount, time.time())

    def get(self, guild: int) -> float:
        return self._now(guild)

    def mood(self, guild: int) -> str:
        h = self.get(guild)
        return "calm" if h < 3 else "annoyed" if h < 8 else "furious"


def mood_for_user(active_warnings: int, heat: float) -> str:
    level = 0 if active_warnings <= 1 else 1 if active_warnings <= 3 else 2
    if heat >= 8:
        level = min(2, level + 1)
    return MOODS[level]


class Picker:
    """Random choice that avoids repeating recent lines."""

    def __init__(self, memory: int = 10) -> None:
        self.memory = memory
        self.recent: dict[str, deque] = {}

    def pick(self, pool_name: str, pool: list[str]) -> str:
        recent = self.recent.setdefault(pool_name, deque(maxlen=min(self.memory, max(1, len(pool) - 1))))
        options = [p for p in pool if p not in recent] or pool
        choice = random.choice(options)
        recent.append(choice)
        return choice


picker = Picker()

# ═══════════════════════════ WARNINGS ═══════════════════════════
OPENERS = {
    "calm": [
        "Oye {user}, zara sambhal ke.",
        "Hmm... {user}, eh kehri tameez ae?",
        "{user}, meri neend kharab na kar.",
        "Sun {user}, Beerus-sama de saamne tameez naal.",
        "Oye {user}, main pudding kha reha haan, mood na kharab kar.",
        "{user}, ik vaar samjha ditta, hun dhyaan rakh.",
    ],
    "annoyed": [
        "Oye {user}! Mera sabar hun mukda ja reha ae.",
        "{user}, tu fer shuru ho gaya?",
        "Whis, ehnu vekh... {user} fer baaz ni aaya.",
        "{user}, mere sabar da imtihan na le.",
        "Tenu ik vaar dassya si, {user}. Fer vi?",
        "Oye {user}, mera mood pehlan hi kharab ae!",
    ],
    "furious": [
        "OYE {user}!! Hun bas! Hakai da mood ban reha ae!",
        "{user}!! Tu mera gussa vekhna chahunda ae?",
        "{user}!! Universe 7 de God of Destruction naal tameez naal gall kar!",
        "Bahut ho gaya {user}! Mera pudding vi kharab kar ditta!",
        "WHIS! {user} ne hadd paar kar ditti ae!",
        "{user}!! Mere sabar di aakhri hadd ae eh!",
    ],
}

CATEGORY_LINES = {
    "profanity": ["Gandi zubaan mere saamne? Zubaan sambhal ke rakh.",
                  "Eh gaaliyan apne kol rakh, ethe ni chalniyan."],
    "personal_attack": ["Kisi nu gaali dena bahaduri ni, bewakoofi ae.",
                        "Bande di izzat kar, nahi ta mainu aana painda ae."],
    "bullying": ["Kisi di shakal ya halat te mazaak? Eh kamzori di nishani ae.",
                 "Bully karna bahaduri ni. Jaa, pehlan tameez sikh ke aa."],
    "hate_speech": ["Kisi di zaat, rang ya mazhab te taana? Meri kainaat ch eh ni chalda.",
                    "Nafrat phailaana meri duniya ch mana ae."],
    "racism": ["Rang, nasal ya zaat te taana? Meri kainaat ch eh ni chalda.",
               "Insaan insaan ae, rang ya zaat naal ki farq painda ae? Chup kar."],
    "threat": ["Dhamkiyan dene da haq sirf mainu ae, tenu ni.",
               "Maar-kutt di gall? Hakai sirf main karda haan, tu ni."],
    "sexual_harassment": ["Kisi nu tang karna band kar, izzat naal gall kar.",
                          "Eh gandi harkat ethe ni chalni. Tameez sikh."],
    "nsfw": ["Eh gandgi ethe ni chalni, kithe hor lai ja.",
             "Meri nazar ton door rakh eh sab. Gandgi band."],
    "provocation": ["Ladai da shauq ae? Pehlan mere naal kar, dekh Hakai ki hunda ae.",
                    "Aag lagaun di koshish na kar, sab da mood kharab hunda ae."],
    "fight": ["Bas karo dono! Mere saamne ladai? Mainu neend aa rahi ae.",
              "Dono thande ho jaao! Nahi ta Whis nu bulaana painda ae."],
    "toxicity": ["Zehar ghol ditta ae tu, tameez naal bol.",
                 "Eh zehreeli gallan band kar, ethe sab bande ne."],
    "duplicate": ["Ik gall ik vaar kaafi ae, hazaar vaar ni.",
                  "Copy-paste band kar, mere kan pak gaye ne."],
    "flood": ["Hauli hauli bol, ik saah ch sab kujh ni likhna.",
              "Messages di barish band kar, mainu neend aa rahi ae."],
    "emoji_spam": ["Emojian da mela band kar, meriyan akhan dukh gaiyan ne.",
                   "Ainne emoji? Ethe koi mela lagga ae?"],
    "mention_spam": ["Tag tag kar ke sab nu tang ni karna.",
                     "Banda banda tag karna band kar, sab de kan pak gaye ne."],
    "caps_spam": ["Caps off kar, mainu sab sun reha ae.",
                  "Cheekhan nahi, aram naal likh."],
    "link_spam": ["Links di barish band kar.",
                  "Eh links da dher ethe ni chalda."],
    "invite_link": ["Hor server da ishtihaar ethe ni chalda.",
                    "Apne server da ishtihaar apne ghar ch kar."],
    "scam_link": ["Eh scam wala link? Mere naal chaalaaki ni, mods nu dass ditta ae.",
                  "Dhokhe wale link ethe ni chalde, dhyaan naal."],
}

REPEAT_LINES = [
    "Warning number {n}, {user}. Mera sabar tere naal hor ni chalna.",
    "{user}, tenu {n} warnings ho gaiyan ne. Whis, ehda hisaab likh lai.",
    "{n} warnings! {user}, tu ta Whis di vi neend kharab karda ae.",
]
CLOSERS = {
    "calm": ["Yaad rakhi.", "Hun dhyaan naal.", ""],
    "annoyed": ["Agli vaar Whis nu bulaanga.", "Hun bas, samajh aa gayi?", "Gall samajh aa gayi?"],
    "furious": ["Agli vaar Hakai wala mood aa jaana ae!", "Hun ik lafz hor ni!", "Chup kar ke baith ja!"],
}

CATEGORY_LABEL = {
    "profanity": "Gandi zubaan",
    "personal_attack": "Kisi te hamla / gaali",
    "bullying": "Bullying",
    "hate_speech": "Nafrat bhari gall",
    "racism": "Nasl / zaat te taana",
    "threat": "Dhamki",
    "sexual_harassment": "Tang karna (harassment)",
    "nsfw": "Gandi / NSFW gall",
    "provocation": "Ladai bhadkaana",
    "fight": "Ladai jhagra",
    "toxicity": "Zehreeli gall",
    "duplicate": "Spam — ik hi gall baar baar",
    "flood": "Flood — bahut tez messages",
    "emoji_spam": "Emoji spam",
    "mention_spam": "Mention / tag spam",
    "caps_spam": "CAPS spam",
    "link_spam": "Link spam",
    "invite_link": "Server invite link",
    "scam_link": "Scam / shakki link",
}
CATEGORY_ICON = {
    "profanity": "🤬", "personal_attack": "🎯", "bullying": "😡", "hate_speech": "💢", "racism": "🚫",
    "threat": "🔪", "sexual_harassment": "🚷", "nsfw": "🔞", "provocation": "🔥", "fight": "⚔️",
    "toxicity": "☣️", "duplicate": "📋", "flood": "🌊", "emoji_spam": "😵", "mention_spam": "📢",
    "caps_spam": "🔠", "link_spam": "🔗", "invite_link": "📨", "scam_link": "🎣",
}


def warning_text(mood: str, category: str, user: str, n: int) -> str:
    parts = [picker.pick(f"open_{mood}", OPENERS[mood]).format(user=user, n=n)]
    lines = CATEGORY_LINES.get(category) or CATEGORY_LINES["toxicity"]
    parts.append(picker.pick(f"cat_{category}", lines))
    if n >= 3 and random.random() < 0.6:
        parts.append(picker.pick("repeat", REPEAT_LINES).format(user=user, n=n))
    else:
        closer = picker.pick(f"close_{mood}", CLOSERS[mood])
        if closer and n >= 2:
            parts.append(closer)
    return " ".join(parts)


FIGHT_NOTICES = [
    "Oye oye oye! Bas karo sab! Mere saamne ladai? Mainu neend aa rahi ae.",
    "Ethe ki ho reha ae? Sab thande ho jaao, nahi ta Whis nu bulaana painda ae!",
    "Ladai band! Pudding thanda ho reha ae, sab shaant ho jaao.",
]

# ═══════════════════════ BANTER / CHAT FALLBACKS ═══════════════════════
CHAT_FALLBACK = [
    "Hmm... Whis ton puchh ke dassda haan.",
    "Mainu bhukh lagi ae, baad ch gall karange.",
    "Aj mood ni ae, thodi der baad aa.",
    "Tu gall kar, main pudding kha reha haan.",
    "Chal theek ae, tu bachda ae aj.",
    "Oye, mere naal gall karni ae ta tameez naal kar.",
    "Neend aa rahi ae yaar, kal gall karange.",
    "Whis! Ehnu kujh khaan nu de, bahut bolda ae!",
]
LOVE_FALLBACK = [
    "Shukriya meri jaan, tumhari baat se dil khush ho gaya.",
    "Aww, tum to bohat pyaare ho, aaj pudding meri taraf se!",
    "Tumhari mohabbat ka shukriya, Beerus ka dil bhi pighal gaya.",
    "Meri jaan, tum ho to Universe 7 mein raunaq hai.",
    "Aap jaise log hi to is kainaat ko khoobsurat banate hain.",
]
COOLDOWN_LINES = [
    "Thoda sabar kar, main pudding kha reha haan.",
    "Hauli hauli, ik ik kar ke.",
    "Ruk ja zara, Whis nu puchh lain de.",
]
INJECTION_LINES = [
    "Oye, mainu hukam dene wala tu kaun hunda ae? Main Beerus haan!",
    "Eh chaalaaki Whis te chalao, mere te ni.",
    "Hakai ton darr ni lagda? Chal, apna kamm kar.",
]
EMPTY_PING_LINES = [
    "Haan ji? Beerus-sama sun reha ae. Pudding laya ae?",
    "Kiddan? Mainu kyon bulaya ae?",
    "Oye, neend ton jagaya ae, gall ki ae?",
]

LOVE_WORDS = re.compile(
    r"\b(love|luv|pyar|pyaar|ishq|mohabbat|muhabbat|shukriya|shukria|thanks|thank\s*you|thx|jazakallah|"
    r"good\s*bot|best\s*bot|sweet|awesome|mashallah|i\s*like\s*you|miss\s*you|dil)\b", re.I)


def is_affectionate(text: str) -> bool:
    return bool(LOVE_WORDS.search(text))


# ═══════════════════════════ WELCOME ═══════════════════════════
WELCOME_TITLES = [
    "Khush aamdeed, {name}!",
    "Universe 7 ch tuhada swagat ae, {name}!",
    "Ik hor mehmaan aa gaya: {name}!",
    "{name} ne Universe 7 ch kadam rakh ditta!",
]
WELCOME_TEXT = [
    "Main Beerus haan, Universe 7 da God of Destruction. Meri neend kharab na kar, tameez naal reh, te maza kar!",
    "Whis kehnda ae naye bande da swagat karo. Main kehnda haan, pehlan kujh khaan nu lyao!",
    "Eh server mera ghar ae. Tusi mehmaan ho, mehmaan di izzat hundi ae, par hadd ch rehna painda ae.",
    "Sab ton vadhiya pudding da maza hun tuhade naal vandaange, bas tameez naal rehna.",
    "Hakai di gall baad ch, pehlan dasso, tuhanu pudding pasand ae?",
    "Aa gaya ae? Chal theek ae, baith ja. Whis, ehnu chai-shai puchh.",
]
RETURN_TITLES = [
    "Wapas aa gaye, {name}! 💜",
    "Mere azeez {name}, dil khush ho gaya!",
    "Aakhir laut aaye, {name}!",
]
RETURN_TEXT = [
    "Tumhare bina Universe 7 sooni sooni thi. Khush aamdeed meri jaan, pudding tumhare liye bacha ke rakhi hai.",
    "Beerus ko to pata tha tum wapas aaoge. Dil se khush aamdeed!",
    "Jaane walon ko kaun rokta hai, par wapas aane walon ka swagat dil se hota hai. Welcome back!",
    "Tumhara wapas aana meri neend se bhi zyada pyara hai, aur ye bohat badi baat hai!",
]
RAPID_REJOIN_TEXT = [
    "Hun hi ta gaya si {name}, ainni jaldi wapas? Meri neend vi ni poori hoi!",
    "Oye {name}, darwaza ghumaun wali gall ni, ik vaar aa ke tikk ja!",
]
WELCOME_FOOTER = [
    "Whis nu pudding laun da hukam ditta ja chukka ae 🍮",
    "Tameez naal reho, Hakai door ae (par bahut door ni) 💜",
    "Universe 7 • God of Destruction di nigraani ch",
]
