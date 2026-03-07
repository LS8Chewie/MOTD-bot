import datetime
import logging
import os
import random

import discord
from discord.ext import tasks

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID")
MEME_FOLDER = os.getenv("MEME_FOLDER", "memes")
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR_UTC", "18"))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE_UTC", "28"))
POST_ON_STARTUP = os.getenv("POST_ON_STARTUP", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = BOT_DIR
LOG_FILE = os.path.join(LOG_DIR, "memebot.log")
SENT_MEMES_FILE = os.path.join(BOT_DIR, "sent_memes.txt")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("memebot")

logger.info("\n%s", "=" * 72)
logger.info("Starting new memebot instance (pid=%s)", os.getpid())

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

if not CHANNEL_ID_RAW or not CHANNEL_ID_RAW.isdigit():
    raise RuntimeError("Missing or invalid CHANNEL_ID environment variable (must be numeric).")

CHANNEL_ID = int(CHANNEL_ID_RAW)

intents = discord.Intents.default()
client = discord.Client(intents=intents)


def load_sent_memes() -> set[str]:
    if not os.path.exists(SENT_MEMES_FILE):
        return set()

    with open(SENT_MEMES_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_sent_memes(sent_memes: set[str]) -> None:
    with open(SENT_MEMES_FILE, "w", encoding="utf-8") as f:
        for meme in sorted(sent_memes):
            f.write(f"{meme}\n")


@tasks.loop(
    time=datetime.time(
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        tzinfo=datetime.timezone.utc,
    )
)
async def meme_of_the_day():
    await post_memes(is_startup=False)


async def post_memes(is_startup: bool) -> None:
    channel = client.get_channel(CHANNEL_ID)

    if channel is None:
        channel = await client.fetch_channel(CHANNEL_ID)

    if is_startup:
        await channel.send("⚙️ **Testing mode**\n> Posting memes...")
    else:
        await channel.send("✅ **Normal operation**\n> Posting memes...")

    memes = [
        f
        for f in os.listdir(MEME_FOLDER)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
    ]

    if not memes:
        logger.warning("No memes found in '%s'.", MEME_FOLDER)
        return

    sent_memes = load_sent_memes()
    available_memes = [meme for meme in memes if meme not in sent_memes]

    if not available_memes:
        logger.warning(
            "No unsent memes available. All memes in '%s' are already recorded in %s.",
            MEME_FOLDER,
            SENT_MEMES_FILE,
        )
        return

    selected_memes = random.sample(available_memes, min(5, len(available_memes)))

    for meme in selected_memes:
        meme_path = os.path.join(MEME_FOLDER, meme)
        await channel.send(file=discord.File(meme_path))
        sent_memes.add(meme)
        logger.info("Posted meme: %s", meme)

    save_sent_memes(sent_memes)
    logger.info("Updated sent-meme memory at %s", SENT_MEMES_FILE)

    if not is_startup:
        await channel.send("✅ **Normal operation complete**")

    logger.info("All memes sent successfully. Shutting down bot.")
    await client.close()


@meme_of_the_day.before_loop
async def before_meme_loop():
    await client.wait_until_ready()


@client.event
async def on_ready():
    logger.info("Logged in as %s", client.user)
    logger.info("Writing logs to %s", LOG_FILE)
    logger.info("Tracking sent memes in %s", SENT_MEMES_FILE)
    logger.info(
        "Next scheduled post will happen at %02d:%02d UTC",
        SCHEDULE_HOUR,
        SCHEDULE_MINUTE,
    )

    if POST_ON_STARTUP:
        logger.info("POST_ON_STARTUP is enabled; posting in testing mode now.")
        await post_memes(is_startup=True)

    if not meme_of_the_day.is_running():
        meme_of_the_day.start()


try:
    client.run(TOKEN)
except KeyboardInterrupt:
    logger.info("Bot stopped manually.")
