# main.py
# Discord Role-based DM Scheduler Bot
# Python 3.9+
# discord.py 2.x

import discord
from discord.ext import commands, tasks
import json
import os
from dotenv import load_dotenv
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
    "is_running": False,
    "progress_channel_id": None
    ,"excluded_user_ids": []
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
intents.message_content = True

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
        if not m.bot and m.id not in config.get("excluded_user_ids", [])
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
@bot.command(help="Lock the bot to the current guild (server).")
@admin_only()
async def setguild(ctx):
    config["guild_id"] = ctx.guild.id
    save_json(CONFIG_PATH, config)
    await ctx.send("Guild locked for this bot.")


@bot.command(help="Set the target role to DM members of.")
@admin_only()
async def setrole(ctx, role: discord.Role):
    config["target_role_id"] = role.id
    progress["member_index"] = 0
    save_json(CONFIG_PATH, config)
    save_json(PROGRESS_PATH, progress)
    await ctx.send(f"Target role set to {role.name}")


@bot.command(help="Set delay (seconds) between DMs.")
@admin_only()
async def setdelay(ctx, seconds: int):
    config["dm_delay_seconds"] = max(1, seconds)
    save_json(CONFIG_PATH, config)
    await ctx.send(f"DM delay set to {seconds} seconds")


@bot.command(help="Set batch size and optional batch delay in seconds. Usage: !setbatch <size> [delay]")
@admin_only()
async def setbatch(ctx, size: int, delay: int = None):
    config["batch_size"] = max(1, size)
    # if delay not provided, keep existing or fall back to 60
    if delay is None:
        delay = config.get("batch_delay_seconds", 60)
    config["batch_delay_seconds"] = max(0, delay)
    save_json(CONFIG_PATH, config)
    await ctx.send(f"Batch settings updated: {config['batch_size']} msgs / {config['batch_delay_seconds']}s")


@bot.command(help="Add a DM message template.")
@admin_only()
async def addtemplate(ctx, *, text: str):
    data = load_json(TEMPLATES_PATH)
    data.setdefault("templates", []).append(text)
    save_json(TEMPLATES_PATH, data)
    await ctx.send("Template added")


@bot.command(name="listtemplates", aliases=["listtemplate", "templates"], help="List saved DM templates.")
@admin_only()
async def listtemplates(ctx):
    data = load_json(TEMPLATES_PATH)
    templates = data.get("templates", [])
    if not templates:
        await ctx.send("No templates saved.")
        return

    lines = [f"{i+1}. {t}" for i, t in enumerate(templates)]
    await ctx.send("\n".join(lines))


@bot.command(help="Delete a saved template by its 1-based index. Usage: !deletetemplate <index>")
@admin_only()
async def deletetemplate(ctx, index: int):
    data = load_json(TEMPLATES_PATH)
    templates = data.get("templates", [])
    if index < 1 or index > len(templates):
        await ctx.send("Invalid template index")
        return

    removed = templates.pop(index-1)
    data["templates"] = templates
    save_json(TEMPLATES_PATH, data)
    await ctx.send(f"Removed template: {removed}")


@bot.command(help="Start sending DMs to members of the configured role.")
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


@bot.command(help="Add a user ID to the exclude list so they will not be DMed.")
@admin_only()
async def addexclude(ctx, user_id: int):
    ex = config.setdefault("excluded_user_ids", [])
    if user_id in ex:
        await ctx.send("User already excluded.")
        return
    ex.append(user_id)
    save_json(CONFIG_PATH, config)
    await ctx.send(f"Excluded user ID: {user_id}")


@bot.command(help="Remove a user ID from the exclude list.")
@admin_only()
async def removeexclude(ctx, user_id: int):
    ex = config.setdefault("excluded_user_ids", [])
    if user_id not in ex:
        await ctx.send("User ID not in exclude list.")
        return
    ex.remove(user_id)
    save_json(CONFIG_PATH, config)
    await ctx.send(f"Removed exclude: {user_id}")


@bot.command(help="List excluded user IDs.")
@admin_only()
async def listexcludes(ctx):
    ex = config.get("excluded_user_ids", [])
    if not ex:
        await ctx.send("No excluded users.")
        return

    guild = get_guild()
    lines = []
    for uid in ex:
        member = None
        if guild:
            member = guild.get_member(uid)

        if member:
            # Member string is like Name#discriminator
            lines.append(f"{uid} - {member}")
            continue

        user = bot.get_user(uid)
        if user:
            lines.append(f"{uid} - {user}")
            continue

        lines.append(f"{uid} - (not found)")

    await ctx.send("\n".join(lines))


@bot.command(help="Set channel where progress updates will be posted. Usage: !setprogresschannel #channel")
@admin_only()
async def setprogresschannel(ctx, channel: discord.TextChannel):
    config["progress_channel_id"] = channel.id
    save_json(CONFIG_PATH, config)
    await ctx.send(f"Progress channel set to {channel.mention}")


@bot.command(help="Send current progress to a channel or the configured progress channel. Usage: !sendprogress [#channel]")
@admin_only()
async def sendprogress(ctx, channel: discord.TextChannel = None):
    # choose provided channel or saved one
    target = channel
    if target is None:
        chan_id = config.get("progress_channel_id")
        if chan_id:
            target = bot.get_channel(chan_id)

    if target is None:
        await ctx.send("No progress channel provided or configured.")
        return

    guild = get_guild()
    role = guild.get_role(config.get("target_role_id")) if guild else None

    # compute member counts and estimated remaining time
    total_members = 0
    remaining = 0
    est_seconds = 0
    if guild and role:
        members = [m for m in role.members if not m.bot]
        total_members = len(members)
        member_index = progress.get("member_index", 0)
        remaining = max(0, total_members - member_index)

        dm_delay = config.get("dm_delay_seconds", 5)
        batch_size = config.get("batch_size", 25)
        batch_delay = config.get("batch_delay_seconds", 60)

        if remaining > 0:
            full_batches = remaining // batch_size
            est_seconds = remaining * dm_delay + full_batches * batch_delay

    def _fmt_seconds(sec: int) -> str:
        sec = int(sec)
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        if h:
            return f"{h}h {m}m {s}s"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"

    est_str = _fmt_seconds(est_seconds) if remaining > 0 else "0s"

    msg = (
        f"Running: {config.get('is_running')}\n"
        f"Guild: {guild.name if guild else 'Not set'}\n"
        f"Role: {role.name if role else 'Not set'}\n"
        f"Delay: {config.get('dm_delay_seconds')}s\n"
        f"Batch: {config.get('batch_size')} msgs / {config.get('batch_delay_seconds')}s\n"
        f"Progress index: {progress.get('member_index')}\n"
        f"Total sent: {progress.get('total_sent')}\n"
        f"Total members: {total_members}\n"
        f"Remaining: {remaining}\n"
        f"Estimated time remaining: {est_str}"
    )

    await target.send(f"```{msg}```")


@bot.command(help="Stop the DM sending process.")
@admin_only()
async def stopdm(ctx):
    stop_event.set()
    config["is_running"] = False
    save_json(CONFIG_PATH, config)
    await ctx.send("DM sending stopped")


@bot.command(help="Show current bot configuration and progress.")
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

    # Load .env (if present) then read DISCORD_TOKEN
    load_dotenv()
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        raise RuntimeError("Set DISCORD_TOKEN environment variable")

    bot.run(TOKEN)
