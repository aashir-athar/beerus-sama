"""Entry point. Handles login errors, reconnect backoff and graceful shutdown (SIGTERM from CI)."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler

import aiohttp
import discord
from dotenv import load_dotenv

from beerus.bot import Beerus
from beerus.config import DATA_DIR


def setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(DATA_DIR / "beerus.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


async def run() -> int:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        print("❌ DISCORD_TOKEN missing. Put it in .env (local) or GitHub Secrets (Actions).", file=sys.stderr)
        return 2
    setup_logging()
    log = logging.getLogger("beerus.main")
    if not os.getenv("GROQ_API_KEY"):
        log.warning("GROQ_API_KEY not set — AI replies off; rule-based moderation still works.")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    current: dict[str, Beerus | None] = {"bot": None}

    def request_stop() -> None:
        log.info("Stop signal received.")
        stop.set()
        bot = current["bot"]
        if bot:
            asyncio.ensure_future(bot.close())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:          # Windows
            pass

    backoff = 30
    while not stop.is_set():
        bot = Beerus()
        current["bot"] = bot
        try:
            async with bot:
                await bot.start(token, reconnect=True)
            backoff = 30
        except discord.LoginFailure:
            log.critical("Discord rejected the token. Reset it in the Developer Portal and update DISCORD_TOKEN.")
            return 3
        except discord.PrivilegedIntentsRequired:
            log.critical("Enable 'Message Content Intent' and 'Server Members Intent' in the Developer Portal → Bot.")
            return 4
        except (aiohttp.ClientError, discord.GatewayNotFound, discord.ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
            log.error("Connection problem (%r). Retrying in %ss…", exc, backoff)
        except Exception:
            log.exception("Fatal error in bot loop. Restarting in %ss…", backoff)
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, 600)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        sys.exit(0)
