#!/usr/bin/env python3
"""Discord selfbot listener that forwards matching messages to a hook.

WARNING: selfbots (a user token instead of a bot token) violate Discord's ToS.
Use at your own risk; a banned account is possible. A real bot (invited to the
servers) is the safe alternative with identical latency.

On a matching message it POSTs JSON to DISCORD_FORWARD_URL:
  { "channelId", "channel", "author", "content", "url", "matched", "at" }

Environment:
  DISCORD_USER_TOKEN    user token (required)
  DISCORD_CHANNELS      comma-separated channel IDs (empty = every channel you see)
  DISCORD_KEYWORDS      comma-separated keywords/regex (case-insensitive)
  DISCORD_FORWARD_URL   HTTP POST target (e.g. http://127.0.0.1:8791/trigger)
  DISCORD_FORWARD_TOKEN optional bearer token for the target
  DISCORD_GUILDS        optional comma-separated guild IDs to restrict
"""

import asyncio
import json
import os
import re
import time
import urllib.request

import discord

TOKEN = os.environ.get("DISCORD_USER_TOKEN", "").strip()
CHANNELS = {c.strip() for c in os.environ.get("DISCORD_CHANNELS", "").split(",") if c.strip()}
GUILDS = {g.strip() for g in os.environ.get("DISCORD_GUILDS", "").split(",") if g.strip()}
FORWARD_URL = os.environ.get("DISCORD_FORWARD_URL", "").strip()
FORWARD_TOKEN = os.environ.get("DISCORD_FORWARD_TOKEN", "").strip()

_RAW_KEYWORDS = [k.strip() for k in os.environ.get("DISCORD_KEYWORDS", "queue,warteschlange,pokemoncenter,pokemon center").split(",") if k.strip()]
KEYWORDS = [re.compile(k, re.IGNORECASE) for k in _RAW_KEYWORDS]


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def matched(content: str) -> str | None:
    for pattern in KEYWORDS:
        found = pattern.search(content)
        if found:
            return found.group(0)
    return None


def forward(payload: dict) -> None:
    if not FORWARD_URL:
        log(f"MATCH (kein Ziel gesetzt): {payload['content'][:160]}")
        return
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(FORWARD_URL, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    if FORWARD_TOKEN:
        request.add_header("Authorization", f"Bearer {FORWARD_TOKEN}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            log(f"TRIGGER gesendet -> {FORWARD_URL} ({response.status})")
    except Exception as exc:
        log(f"TRIGGER fehlgeschlagen: {exc}")


client = discord.Client()


@client.event
async def on_ready():
    log(f"Discord-Listener bereit als {client.user} | Channels={len(CHANNELS) or 'alle'} | Keywords={_RAW_KEYWORDS}")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    if GUILDS and str(getattr(message.guild, "id", "")) not in GUILDS:
        return
    if CHANNELS and str(message.channel.id) not in CHANNELS:
        return

    content = message.content or ""
    # Also scan embeds (groups often post links via embeds).
    for embed in message.embeds:
        content += " " + " ".join(filter(None, [embed.title, embed.description, embed.url]))

    hit = matched(content)
    if not hit:
        return

    payload = {
        "channelId": str(message.channel.id),
        "channel": getattr(message.channel, "name", ""),
        "author": str(message.author),
        "content": content[:1000],
        "url": message.jump_url,
        "matched": hit,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    log(f"MATCH '{hit}' in #{payload['channel']}: {content[:120]}")
    await asyncio.to_thread(forward, payload)


def main() -> None:
    if not TOKEN:
        raise SystemExit("DISCORD_USER_TOKEN fehlt.")
    client.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
