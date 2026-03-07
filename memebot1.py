import datetime
import os
import random

import discord
from discord.ext import tasks

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID")
MEME_FOLDER = os.getenv("MEME_FOLDER", "memes")
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR_UTC", "10"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE_UTC", "0"))
POST_ON_STARTUP = os.getenv("POST_ON_STARTUP", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

if not CHANNEL_ID_RAW or not CHANNEL_ID_RAW.isdigit():
    raise RuntimeError("Missing or invalid CHANNEL_ID environment variable (must be numeric).")

CHANNEL_ID = int(CHANNEL_ID_RAW)

intents = discord.Intents.default()
client = discord.Client(intents=intents)


@tasks.loop(
    time=datetime.time(
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        tzinfo=datetime.timezone.utc,
    )
)
async def meme_of_the_day():
    channel = client.get_channel(CHANNEL_ID)

    if channel is None:
        channel = await client.fetch_channel(CHANNEL_ID)

    memes = [
        f
        for f in os.listdir(MEME_FOLDER)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
    ]

    if not memes:
        print(f"No memes found in '{MEME_FOLDER}'.")
        return

    selected_memes = random.sample(memes, min(5, len(memes)))

    for meme in selected_memes:
        meme_path = os.path.join(MEME_FOLDER, meme)
        await channel.send(file=discord.File(meme_path))
        print("Posted meme:", meme)


@meme_of_the_day.before_loop
async def before_meme_loop():
    await client.wait_until_ready()


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    if POST_ON_STARTUP:
        print("POST_ON_STARTUP is enabled; posting memes now.")
        await meme_of_the_day()
    if not meme_of_the_day.is_running():
        meme_of_the_day.start()


client.run(TOKEN)
