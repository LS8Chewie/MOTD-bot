# NOTE: This file must contain only valid Python source (no pasted git diff hunks like "@@ ... @@").
import asyncio
import datetime
import glob
import logging
import os
import random
import sys
from typing import Optional, Set

import discord
from discord.ext import tasks

logger = logging.getLogger("memebot")

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID_RAW = os.getenv("CHANNEL_ID")
MEME_FOLDER = os.getenv("MEME_FOLDER", "memes")
SCHEDULE_HOUR_RAW = (
    os.getenv("SCHEDULE_HOUR_CST")
    or os.getenv("SCHEDULE_HOUR")
    or os.getenv("SCHEDULE_HOUR_UTC")
    or "18"
)
SCHEDULE_MINUTE_RAW = (
    os.getenv("SCHEDULE_MINUTE_CST")
    or os.getenv("SCHEDULE_MINUTE")
    or os.getenv("SCHEDULE_MINUTE_UTC")
    or "0"
)


def _parse_int_env(raw_value: str, env_name: str) -> int:
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {env_name} value '{raw_value}'. Expected an integer.") from exc


SCHEDULE_HOUR = _parse_int_env(SCHEDULE_HOUR_RAW, "SCHEDULE_HOUR")
SCHEDULE_MINUTE = _parse_int_env(SCHEDULE_MINUTE_RAW, "SCHEDULE_MINUTE")
POST_ON_STARTUP = os.getenv("POST_ON_STARTUP", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ENABLE_PUBLISH = os.getenv("ENABLE_PUBLISH", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SEND_DELAY_SECONDS_RAW = os.getenv("SEND_DELAY_SECONDS", "2")
PUBLISH_DELAY_SECONDS_RAW = os.getenv("PUBLISH_DELAY_SECONDS", "2")
GROUP_MEMES = os.getenv("GROUP_MEMES", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
GROUP_COUNT_RAW = os.getenv("GROUP_COUNT", "1")
UNGROUPED_MESSAGE_COUNT_RAW = os.getenv("UNGROUPED_MESSAGE_COUNT", "3")
GROUP_COUNT = _parse_int_env(GROUP_COUNT_RAW, "GROUP_COUNT")
UNGROUPED_MESSAGE_COUNT = _parse_int_env(UNGROUPED_MESSAGE_COUNT_RAW, "UNGROUPED_MESSAGE_COUNT")
SEND_DELAY_SECONDS = _parse_int_env(SEND_DELAY_SECONDS_RAW, "SEND_DELAY_SECONDS")
PUBLISH_DELAY_SECONDS = _parse_int_env(PUBLISH_DELAY_SECONDS_RAW, "PUBLISH_DELAY_SECONDS")
MAX_FILES_PER_MESSAGE = 3

from zoneinfo import ZoneInfo
CST_TZ = ZoneInfo("America/Chicago")

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = BOT_DIR
MAX_LOG_FILES = 5
SENT_MEMES_FILE = os.path.join(BOT_DIR, "sent_memes.txt")
LOCK_FILE = os.path.join(BOT_DIR, "memebot.lock")


def _configure_log_file() -> str:
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = os.path.join(LOG_DIR, f"memebot-{timestamp}.log")
    log_pattern = os.path.join(LOG_DIR, "memebot-*.log")
    existing_logs = sorted(glob.glob(log_pattern), key=os.path.getmtime)

    while len(existing_logs) >= MAX_LOG_FILES:
        oldest_log = existing_logs.pop(0)
        try:
            os.remove(oldest_log)
        except OSError as exc:
            print(f"Warning: failed to delete old log file {oldest_log}: {exc}", file=sys.stderr)
            break

    return log_file


LOG_FILE = _configure_log_file()


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
    except OSError as exc:
        logger.warning("Could not probe pid %s for lock validation: %s", pid, exc)
        return False

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


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


configure_logging()
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


def schedule_time_has_passed_today_cst() -> bool:
    now = datetime.datetime.now(CST_TZ)
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
            f"Invalid schedule hour '{SCHEDULE_HOUR_RAW}'. Expected 0-23 via SCHEDULE_HOUR_CST, SCHEDULE_HOUR, or SCHEDULE_HOUR_UTC."
        )

    if not (0 <= SCHEDULE_MINUTE <= 59):
        raise RuntimeError(
            f"Invalid schedule minute '{SCHEDULE_MINUTE_RAW}'. Expected 0-59 via SCHEDULE_MINUTE_CST, SCHEDULE_MINUTE, or SCHEDULE_MINUTE_UTC."
        )

    if GROUP_COUNT < 0:
        raise RuntimeError(
            f"Invalid GROUP_COUNT '{GROUP_COUNT_RAW}'. Expected an integer >= 0."
        )

    if SEND_DELAY_SECONDS < 0:
        raise RuntimeError(
            f"Invalid SEND_DELAY_SECONDS '{SEND_DELAY_SECONDS_RAW}'. Expected an integer >= 0."
        )

    if PUBLISH_DELAY_SECONDS < 0:
        raise RuntimeError(
            f"Invalid PUBLISH_DELAY_SECONDS '{PUBLISH_DELAY_SECONDS_RAW}'. Expected an integer >= 0."
        )

    if UNGROUPED_MESSAGE_COUNT < 1:
        raise RuntimeError(
            f"Invalid UNGROUPED_MESSAGE_COUNT '{UNGROUPED_MESSAGE_COUNT_RAW}'. Expected an integer >= 1."
        )


@tasks.loop(
    time=datetime.time(
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        tzinfo=CST_TZ,
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


async def wait_between_sends() -> None:
    if SEND_DELAY_SECONDS > 0:
        logger.info("Waiting %s second(s) before the next send.", SEND_DELAY_SECONDS)
        await asyncio.sleep(SEND_DELAY_SECONDS)


async def send_message(
    channel: discord.abc.Messageable,
    *,
    content: Optional[str] = None,
    files: Optional[list[discord.File]] = None,
) -> discord.Message:
    message = await channel.send(content=content, files=files)
    logger.info("Sent message %s to channel %s.", message.id, CHANNEL_ID)
    return message


async def publish_message(message: discord.Message) -> discord.Message:
    if not ENABLE_PUBLISH:
        logger.info("Skipping publish for message %s because ENABLE_PUBLISH is disabled.", message.id)
        return message

    try:
        await message.publish()
        logger.info("Published message %s to followers.", message.id)
        if PUBLISH_DELAY_SECONDS > 0:
            logger.info(
                "Waiting %s second(s) after publishing message %s to preserve delivery order.",
                PUBLISH_DELAY_SECONDS,
                message.id,
            )
            await asyncio.sleep(PUBLISH_DELAY_SECONDS)
    except (discord.Forbidden, discord.HTTPException) as exc:
        logger.warning("Could not publish message %s: %s", message.id, exc)

    return message


async def post_memes(is_startup: bool) -> None:
    channel = await get_target_channel()

    if is_startup:
        await send_message(channel, content="⚙️ **Testing mode**\n> Posting memes...")
        await wait_between_sends()
    else:
        await send_message(
            channel,
            content=" _**BEGINING MEME INNOCULATION**_\n _entertaining masses..._",
        )
        await wait_between_sends()

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

    random.shuffle(available_memes)
    logger.info(
        "Prepared %s unsent meme(s) from %s total file(s) in '%s'.",
        len(available_memes),
        len(memes),
        MEME_FOLDER,
    )

    posted_memes: list[str] = []

    if GROUP_MEMES and GROUP_COUNT > 0:
        max_memes_to_send = GROUP_COUNT * MAX_FILES_PER_MESSAGE
        selected_memes = available_memes[:max_memes_to_send]
        sent_groups = 0

        for start in range(0, len(selected_memes), MAX_FILES_PER_MESSAGE):
            if sent_groups >= GROUP_COUNT:
                break

            group_memes = selected_memes[start : start + MAX_FILES_PER_MESSAGE]
            batch_files: list[discord.File] = []

            for meme in group_memes:
                meme_path = os.path.join(MEME_FOLDER, meme)
                try:
                    batch_files.append(discord.File(meme_path))
                    posted_memes.append(meme)
                except OSError as exc:
                    logger.warning("Skipping meme '%s' because it could not be opened (%s)", meme, exc)

            if not batch_files:
                logger.warning("No readable meme files were found in selected group; skipping send.")
                continue

            try:
                message = await send_message(channel, files=batch_files)
                await wait_between_sends()
                await publish_message(message)
                sent_groups += 1
                logger.info(
                    "Sent group %s/%s with %s meme file(s).",
                    sent_groups,
                    GROUP_COUNT,
                    len(batch_files),
                )
                await wait_between_sends()
            except (discord.Forbidden, discord.HTTPException) as exc:
                logger.warning("Failed to send meme group starting at index %s: %s", start, exc)
    else:
        selected_memes = available_memes[:UNGROUPED_MESSAGE_COUNT]

        for meme in selected_memes:
            meme_path = os.path.join(MEME_FOLDER, meme)
            try:
                message = await send_message(channel, files=[discord.File(meme_path)])
                await wait_between_sends()
                await publish_message(message)
                posted_memes.append(meme)
                logger.info("Sent meme as individual message: %s", meme)
                await wait_between_sends()
            except OSError as exc:
                logger.warning("Skipping meme '%s' because it could not be opened (%s)", meme, exc)
            except (discord.Forbidden, discord.HTTPException) as exc:
                logger.warning("Failed to send meme '%s': %s", meme, exc)

    if not posted_memes:
        logger.warning("No readable meme files were found in selected send window; skipping send.")
        return

    for meme in posted_memes:
        sent_memes.add(meme)
        logger.info("Queued sent meme: %s", meme)

    save_sent_memes(sent_memes)
    logger.info("Updated sent-meme memory at %s", SENT_MEMES_FILE)

    if not is_startup:
        closing_message = await send_message(
            channel,
            content=" _**MEME INNOCULATION IS COMPLETE. Shutting down.**_",
        )
        await publish_message(closing_message)

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
    logger.info("Message publish is %s", "enabled" if ENABLE_PUBLISH else "disabled")
    logger.info("Delay between sends is %s second(s).", SEND_DELAY_SECONDS)
    logger.info("Publish delay between messages is %s second(s).", PUBLISH_DELAY_SECONDS)
    logger.info(
        "Meme grouping is %s (GROUP_COUNT=%s, UNGROUPED_MESSAGE_COUNT=%s, MAX_FILES_PER_MESSAGE=%s)",
        "enabled" if (GROUP_MEMES and GROUP_COUNT > 0) else "disabled (individual mode)",
        GROUP_COUNT,
        UNGROUPED_MESSAGE_COUNT,
        MAX_FILES_PER_MESSAGE,
    )
    now_cst = datetime.datetime.now(CST_TZ)
    logger.info(
        "Schedule configured from env hour='%s' minute='%s'; effective CST time %02d:%02d.",
        SCHEDULE_HOUR_RAW,
        SCHEDULE_MINUTE_RAW,
        SCHEDULE_HOUR,
        SCHEDULE_MINUTE,
    )
    logger.info(
        "Current CST time is %02d:%02d; next scheduled post is %02d:%02d CST.",
        now_cst.hour,
        now_cst.minute,
        SCHEDULE_HOUR,
        SCHEDULE_MINUTE,
    )

    if POST_ON_STARTUP:
        logger.info("POST_ON_STARTUP is enabled; posting in testing mode now.")
        await post_memes(is_startup=True)
        return

    if schedule_time_has_passed_today_cst():
        logger.info(
            "Scheduled time %02d:%02d CST has already passed today; posting immediately.",
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
