import datetime
import logging
import os
import random
import sys
from typing import Optional, Set

import discord
from discord.ext import tasks

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID")
MEME_FOLDER = os.getenv("MEME_FOLDER", "memes")
SCHEDULE_HOUR_RAW = os.getenv("SCHEDULE_HOUR_UTC") or os.getenv("SCHEDULE_HOUR") or "18"
SCHEDULE_MINUTE_RAW = os.getenv("SCHEDULE_MINUTE_UTC") or os.getenv("SCHEDULE_MINUTE") or "0"
SCHEDULE_HOUR = int(SCHEDULE_HOUR_RAW)
SCHEDULE_MINUTE = int(SCHEDULE_MINUTE_RAW)
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
LOCK_FILE = os.path.join(BOT_DIR, "memebot.lock")


def _release_lock() -> None:
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            logger.info("Released instance lock at %s", LOCK_FILE)
    except OSError as exc:
        logger.warning("Failed to release lock file %s: %s", LOCK_FILE, exc)


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    return True


def acquire_single_instance_lock() -> None:
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                running_pid = int(f.read().strip())
        except (OSError, ValueError):
            running_pid = None

        if running_pid and _pid_is_running(running_pid):
            logger.info(
                "Shutdown reason: another memebot instance is already running (pid=%s).",
                running_pid,
            )
            sys.exit(0)

        logger.warning("Found stale lock file at %s. Replacing it.", LOCK_FILE)

    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    logger.info("Acquired single-instance lock at %s", LOCK_FILE)

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
acquire_single_instance_lock()

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

if not CHANNEL_ID_RAW or not CHANNEL_ID_RAW.isdigit():
    raise RuntimeError("Missing or invalid CHANNEL_ID environment variable (must be numeric).")

CHANNEL_ID = int(CHANNEL_ID_RAW)

intents = discord.Intents.default()
client = discord.Client(intents=intents)


def load_sent_memes() -> Set[str]:
    if not os.path.exists(SENT_MEMES_FILE):
        return set()

    with open(SENT_MEMES_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_sent_memes(sent_memes: Set[str]) -> None:
    with open(SENT_MEMES_FILE, "w", encoding="utf-8") as f:
        for meme in sorted(sent_memes):
            f.write(f"{meme}\n")


def schedule_time_has_passed_today_utc() -> bool:
    now = datetime.datetime.now(datetime.timezone.utc)
    scheduled = now.replace(
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        second=0,
        microsecond=0,
    )

    return now >= scheduled


def validate_schedule_time() -> None:
    if not (0 <= SCHEDULE_HOUR <= 23):
        raise RuntimeError(
            f"Invalid schedule hour '{SCHEDULE_HOUR_RAW}'. Expected 0-23 via SCHEDULE_HOUR_UTC or SCHEDULE_HOUR."
        )

    if not (0 <= SCHEDULE_MINUTE <= 59):
        raise RuntimeError(
            f"Invalid schedule minute '{SCHEDULE_MINUTE_RAW}'. Expected 0-59 via SCHEDULE_MINUTE_UTC or SCHEDULE_MINUTE."
        )


@tasks.loop(
    time=datetime.time(
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        tzinfo=datetime.timezone.utc,
    )
)
async def meme_of_the_day():
    await post_memes(is_startup=False)


async def get_target_channel() -> discord.abc.Messageable:
    channel = client.get_channel(CHANNEL_ID)

    if channel is None:
        channel = await client.fetch_channel(CHANNEL_ID)

    if not hasattr(channel, "send"):
        raise RuntimeError(f"Configured CHANNEL_ID {CHANNEL_ID} does not support sending messages.")

    return channel


async def send_and_publish(
    channel: discord.abc.Messageable,
    *,
    content: Optional[str] = None,
    file: Optional[discord.File] = None,
) -> discord.Message:
    message = await channel.send(content=content, file=file)

    try:
        await message.publish()
        logger.info("Published message %s to followers.", message.id)
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.warning("Could not publish message %s: %s", message.id, exc)

    return message


async def post_memes(is_startup: bool) -> None:
    channel = await get_target_channel()

    if is_startup:
        await send_and_publish(channel, content="⚙️ **Testing mode**\n> Posting memes...")
    else:
        await send_and_publish(channel, content=" _**BEGINING MEME INNOCULATION**_\n _entertaining masses..._")

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
        await send_and_publish(channel, file=discord.File(meme_path))
        sent_memes.add(meme)
        logger.info("Posted meme: %s", meme)

    save_sent_memes(sent_memes)
    logger.info("Updated sent-meme memory at %s", SENT_MEMES_FILE)

    if not is_startup:
        await send_and_publish(channel, content=" _**MEME INNOCULATION IS COMPLETE. Shutting down.**_")

    logger.info("All memes sent successfully. Shutting down bot.")
    await client.close()


@meme_of_the_day.before_loop
async def before_meme_loop():
    await client.wait_until_ready()


@client.event
async def on_ready():
    validate_schedule_time()
    logger.info("Logged in as %s", client.user)
    logger.info("Writing logs to %s", LOG_FILE)
    logger.info("Tracking sent memes in %s", SENT_MEMES_FILE)
    logger.info("Configured target channel from CHANNEL_ID env var: %s", CHANNEL_ID)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    logger.info(
        "Schedule configured from env hour='%s' minute='%s'; effective UTC time %02d:%02d.",
        SCHEDULE_HOUR_RAW,
        SCHEDULE_MINUTE_RAW,
        SCHEDULE_HOUR,
        SCHEDULE_MINUTE,
    )
    logger.info(
        "Current UTC time is %02d:%02d; next scheduled post is %02d:%02d UTC.",
        now_utc.hour,
        now_utc.minute,
        SCHEDULE_HOUR,
        SCHEDULE_MINUTE,
    )

    if POST_ON_STARTUP:
        logger.info("POST_ON_STARTUP is enabled; posting in testing mode now.")
        await post_memes(is_startup=True)
        return

    if schedule_time_has_passed_today_utc():
        logger.info(
            "Scheduled time %02d:%02d UTC has already passed today; posting immediately.",
            SCHEDULE_HOUR,
            SCHEDULE_MINUTE,
        )
        await post_memes(is_startup=False)
        return

    logger.info("Scheduled time has not passed yet; waiting for task loop trigger.")

    if not meme_of_the_day.is_running():
        meme_of_the_day.start()


try:
    client.run(TOKEN)
except KeyboardInterrupt:
    logger.info("Bot stopped manually.")
finally:
    _release_lock()
