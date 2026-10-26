# Beerus-Sama — AI-Powered Discord Moderation Bot

Beerus-Sama is a Python Discord moderation bot for detecting spam and harmful messages in English, Roman Urdu, Roman Hindi, and Roman Punjabi. It combines configurable text rules with optional Groq AI review, Discord slash commands, and persistent warnings. The bot is **warn-only**: it never kicks, mutes, times out, or bans members (`scripts/check_no_punishments.py` enforces this).

Designed for multilingual Discord communities, Beerus-Sama recognizes mixed-language messages, slang, spelling variations, and common evasions. Moderation settings and phrase lists are configurable, while message content is never stored.

## 1 · Secure your keys FIRST
Keys pasted in chats/screenshots must be treated as leaked.
1. Discord Developer Portal → your app → **Bot → Reset Token**.
2. Groq console → **API Keys** → delete the old key, create a new one.
3. Never commit them. They only go in `.env` (local) or **GitHub Secrets** (Actions).

## 2 · Discord Developer Portal
**Bot** tab → enable **Message Content Intent** and **Server Members Intent**.

Re-invite (or edit the bot's role) with **only** these permissions — integer `117824`:
View Channels · Send Messages · Embed Links · Attach Files · Read Message History · Add Reactions.
(No Kick / Ban / Moderate Members = Discord itself stops it from punishing anyone.)
Invite URL: `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=117824`

Make sure the bot can see + write in the **welcome** channel and the private **logs** channel.

## 3 · Run it
**Locally**
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in DISCORD_TOKEN and GROQ_API_KEY
python tests/check_detection.py && python main.py
```

**GitHub Actions (free)** — repo must be **public** (free unlimited minutes; private repos only get 2,000 min/month ≈ 33 h).
Settings → Secrets and variables → Actions → add secrets `DISCORD_TOKEN`, `GROQ_API_KEY`
(optional variable `GROQ_MODEL`). Then Actions → *Beerus-Sama (24/7)* → **Run workflow**.
Each run lasts ~5h40m, queues its own successor, and an hourly cron restarts it if the chain breaks.

⚠️ GitHub's terms say Actions are meant for building/testing software, not hosting services. Many people do this, but
GitHub *can* limit it. For a permanent free home use an always-free VM (e.g. Oracle Cloud Always Free) and run `python main.py`.

**Memory / warnings survive restarts** via two free layers: the Actions cache **and** an automatic backup file posted to your
private logs channel every 6 h (+ on shutdown) that is auto-restored on a fresh machine. No message text is ever stored.

## 4 · Admin commands (`/beerus …`, administrators only)
`status` · `warnings @user` · `clearwarnings @user` · `warn @user reason` · `top` · `test <text>` (dry-run scoring) ·
`backup` · `reload` · `config view|set|reset`

Tune everything in `config/rules.yaml`; add words in `config/lexicon.yaml`; change the voice in `beerus/persona.py`.
Live tweak example: `/beerus config set key:spam.flood.max_messages value:8`.

## 5 · How it decides
`spam rules → word/phrase rules (spellings, censoring, leetspeak) → intent signals (banter, quoting, "talking about a word")
→ score 0-100`. ≥55 warns. 25-55 is a gray zone: Groq reads the last few messages and decides (cheap, budgeted).
If Groq is down or out of free quota, messages ≥45 still warn locally.

## 6 · Free-tier budget (Groq)
Defaults stay under: 30 req/min, 1,000 req/day, 200k tokens/day. See `ai:` in `rules.yaml`. `/beerus status` shows today's usage.

## Honest notes
* Roman Punjabi has no official spelling; one consistent Lahori-style scheme is used (see top of `persona.py`). Fixed lines
  are hand-written; free-form AI replies depend on the model — tell me any spelling you want changed.
* No word list is perfect. Use `/beerus test` on real examples from your server and adjust `lexicon.yaml`.
* The bot was unit-tested offline (detection, AI safety filters). The Discord-side code (embeds, slash commands) needs
  your first live run — check the logs channel and `data/beerus.log`.
