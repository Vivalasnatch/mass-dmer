# main.py
# Discord Role-based DM Scheduler Bot
# Python 3.9+
# discord.py 2.x

import discord
from discord.ext import commands, tasks
import json
import os
import random
import asyncio
from typing import List

# -----------------------------
# Paths & constants
# -----------------------------
DATA_DIR = "data"
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
TEMPLATES_PATH = os.path.join(DATA_DIR, "templates.json")
PROGRESS_PATH = os.path.join(DATA_DIR, "progress.json")

DEFAULT_CONFIG = {
    "guild_id": None,
    "target_role_id": None,
    "dm_delay_seconds": 5,
    "batch_size": 25,
    "batch_delay_seconds": 60,
    "is_running": False
}

DEFAULT_PROGRESS = {
    "member_index": 0,
    "total_sent": 0
}

# -----------------------------
# Helpers for JSON persistence
# -----------------------------

def ensure_data_files():
    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.exists(CONFIG_PATH):
        save_json(CONFIG_PATH, DEFAULT_CONFIG)

    if not os.path.exists(TEMPLATES_PATH):
        save_json(TEMPLATES_PATH, {"templates": []})

    if not os.path.exists(PROGRESS_PATH):
        save_json(PROGRESS_PATH, DEFAULT_PROGRESS)


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


# -----------------------------
# Bot setup
# -----------------------------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory state (mirrors JSON)
config = {}
progress = {}

# Used to stop a running DM loop safely
stop_event = asyncio.Event()


# -----------------------------
# Utility checks
# -----------------------------

def admin_only():
    async def predicate(ctx):
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)


def get_guild():
    if not config.get("guild_id"):
        return None
    return bot.get_guild(config["guild_id"])


# -----------------------------
# DM sending logic
# -----------------------------
async def dm_scheduler():
    global progress, config

    guild = get_guild()
    if guild is None:
        return

    role_id = config.get("target_role_id")
    if not role_id:
        return

    role = guild.get_role(role_id)
    if role is None:
        return

    templates_data = load_json(TEMPLATES_PATH)
    templates: List[str] = templates_data.get("templates", [])
    if not templates:
        return

    members = [
        m for m in role.members
        if not m.bot
    ]

    member_index = progress.get("member_index", 0)
    sent_in_batch = 0

    while member_index < len(members):
        if stop_event.is_set():
            break

        member = members[member_index]
        message = random.choice(templates)

        try:
            await member.send(message)
            progress["total_sent"] += 1
        except discord.Forbidden:
            pass  # DMs closed
        except discord.HTTPException:
            pass  # Network / rate issue

        member_index += 1
        progress["member_index"] = member_index
        save_json(PROGRESS_PATH, progress)

        sent_in_batch += 1

        # Per-message delay
        await asyncio.sleep(config.get("dm_delay_seconds", 5))

        # Batch handling
        if sent_in_batch >= config.get("batch_size", 25):
            sent_in_batch = 0
            await asyncio.sleep(config.get("batch_delay_seconds", 60))

    config["is_running"] = False
    save_json(CONFIG_PATH, config)


# -----------------------------
# Events
# -----------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


# -----------------------------
# Commands (Admin only)
# -----------------------------
@bot.command()
@admin_only()
async def setguild(ctx):
    config["guild_id"] = ctx.guild.id
    save_json(CONFIG_PATH, config)
    await ctx.send("Guild locked for this bot.")


@bot.command()
@admin_only()
async def setrole(ctx, role: discord.Role):
    config["target_role_id"] = role.id
    progress["member_index"] = 0
    save_json(CONFIG_PATH, config)
    save_json(PROGRESS_PATH, progress)
    await ctx.send(f"Target role set to {role.name}")


@bot.command()
@admin_only()
async def setdelay(ctx, seconds: int):
    config["dm_delay_seconds"] = max(1, seconds)
    save_json(CONFIG_PATH, config)
    await ctx.send(f"DM delay set to {seconds} seconds")


@bot.command()
@admin_only()
async def setbatch(ctx, size: int, delay: int):
    config["batch_size"] = max(1, size)
    config["batch_delay_seconds"] = max(0, delay)
    save_json(CONFIG_PATH, config)
    await ctx.send("Batch settings updated")


@bot.command()
@admin_only()
async def addtemplate(ctx, *, text: str):
    data = load_json(TEMPLATES_PATH)
    data.setdefault("templates", []).append(text)
    save_json(TEMPLATES_PATH, data)
    await ctx.send("Template added")


@bot.command()
@admin_only()
async def startdm(ctx):
    if config.get("is_running"):
        await ctx.send("DM process already running")
        return

    stop_event.clear()
    config["is_running"] = True
    save_json(CONFIG_PATH, config)

    bot.loop.create_task(dm_scheduler())
    await ctx.send("DM sending started")


@bot.command()
@admin_only()
async def stopdm(ctx):
    stop_event.set()
    config["is_running"] = False
    save_json(CONFIG_PATH, config)
    await ctx.send("DM sending stopped")


@bot.command()
@admin_only()
async def status(ctx):
    guild = get_guild()
    role = guild.get_role(config.get("target_role_id")) if guild else None

    msg = (
        f"Running: {config.get('is_running')}\n"
        f"Guild: {guild.name if guild else 'Not set'}\n"
        f"Role: {role.name if role else 'Not set'}\n"
        f"Delay: {config.get('dm_delay_seconds')}s\n"
        f"Batch: {config.get('batch_size')} msgs / {config.get('batch_delay_seconds')}s\n"
        f"Progress index: {progress.get('member_index')}\n"
        f"Total sent: {progress.get('total_sent')}"
    )
    await ctx.send(f"```{msg}```")


# -----------------------------
# Entrypoint
# -----------------------------
if __name__ == "__main__":
    ensure_data_files()
    config = load_json(CONFIG_PATH)
    progress = load_json(PROGRESS_PATH)

    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        raise RuntimeError("Set DISCORD_TOKEN environment variable")

    bot.run(TOKEN)
