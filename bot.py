import os
import json
import asyncio
import logging
from pathlib import Path

import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")

CHANNEL_ID = "@biggtimsxvid"
X_USERNAME = "FERNANDEZFrdric"

X_STREAM_URL = "https://api.x.com/2/tweets/search/stream"
X_RULES_URL = "https://api.x.com/2/tweets/search/stream/rules"

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

SEEN_FILE = Path("seen_posts.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# =========================
# VALIDATE ENVIRONMENT
# =========================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not X_BEARER_TOKEN:
    raise RuntimeError("X_BEARER_TOKEN is missing")


# =========================
# SEEN POSTS
# =========================

def load_seen_posts():
    if not SEEN_FILE.exists():
        return set()

    try:
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


SEEN_POSTS = load_seen_posts()


def save_seen_posts():
    # Keep only the latest 500 IDs
    latest = list(SEEN_POSTS)[-500:]

    with open(SEEN_FILE, "w") as f:
        json.dump(latest, f)


# =========================
# X API HEADERS
# =========================

def x_headers():
    return {
        "Authorization": f"Bearer {X_BEARER_TOKEN}"
    }


# =========================
# STREAM RULE
# =========================

async def ensure_stream_rule():
    """
    Make sure the stream watches:
    from:FERNANDEZFrdric -is:retweet
    """

    rule_value = f"from:{X_USERNAME} -is:retweet"

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(
        headers=x_headers(),
        timeout=timeout
    ) as session:

        # Get existing rules
        async with session.get(X_RULES_URL) as response:
            text = await response.text()

            if response.status != 200:
                logger.error(
                    "❌ Could not get X stream rules: HTTP %s",
                    response.status
                )
                logger.error(text)
                return False

            try:
                result = json.loads(text)
            except Exception:
                logger.error("Invalid X response: %s", text)
                return False

        existing_rules = result.get("data", [])

        for rule in existing_rules:
            if rule.get("value") == rule_value:
                logger.info("✅ Stream rule already exists")
                logger.info("Rule: %s", rule_value)
                return True

        # Add rule
        payload = {
            "add": [
                {
                    "value": rule_value,
                    "tag": "BiggTims X Video Bot"
                }
            ]
        }

        async with session.post(
            X_RULES_URL,
            json=payload
        ) as response:

            text = await response.text()

            if response.status not in (200, 201):
                logger.error(
                    "❌ Could not create X stream rule: HTTP %s",
                    response.status
                )
                logger.error(text)
                return False

            logger.info("✅ X stream rule created")
            logger.info("Rule: %s", rule_value)

            return True


# =========================
# VIDEO DOWNLOAD
# =========================

async def download_video(video_url, post_id):
    """
    Download the X video directly.
    """

    filename = DOWNLOAD_DIR / f"{post_id}.mp4"

    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=30,
        sock_read=120
    )

    try:
        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(video_url) as response:

                if response.status != 200:
                    logger.error(
                        "❌ Video download failed: HTTP %s",
                        response.status
                    )
                    return None

                with open(filename, "wb") as f:

                    while True:
                        chunk = await response.content.read(1024 * 1024)

                        if not chunk:
                            break

                        f.write(chunk)

        logger.info(
            "✅ Video downloaded: %s",
            filename
        )

        return filename

    except Exception as e:
        logger.exception(
            "❌ Video download error: %s",
            e
        )

        return None


# =========================
# FIND BEST VIDEO
# =========================

def find_best_video(event):
    """
    Look inside the streamed X post for video media.
    """

    data = event.get("data", {})
    includes = event.get("includes", {})

    media_items = includes.get("media", [])

    if not media_items:
        return None

    best_video = None
    best_bitrate = -1

    for media in media_items:

        if media.get("type") != "video":
            continue

        variants = media.get("variants", [])

        for variant in variants:

            content_type = variant.get(
                "content_type",
                ""
            )

            url = variant.get("url")

            bitrate = variant.get(
                "bit_rate",
                0
            ) or 0

            if (
                content_type == "video/mp4"
                and url
                and bitrate > best_bitrate
            ):
                best_video = url
                best_bitrate = bitrate

    return best_video


# =========================
# TELEGRAM
# =========================

async def send_video_to_channel(
    application,
    video_path,
    post_id,
    caption
):

    try:

        with open(video_path, "rb") as video:

            await application.bot.send_video(
                chat_id=CHANNEL_ID,
                video=video,
                caption=caption,
                supports_streaming=True
            )

        logger.info(
            "✅ Sent post %s to Telegram",
            post_id
        )

        return True

    except Exception as e:

        logger.exception(
            "❌ Telegram upload failed: %s",
            e
        )

        return False


# =========================
# PROCESS X POST
# =========================

async def process_x_post(
    application,
    event
):

    data = event.get("data", {})

    post_id = data.get("id")

    if not post_id:
        logger.warning(
            "⚠️ Stream event contained no post ID"
        )
        return

    # Prevent duplicates
    if post_id in SEEN_POSTS:
        logger.info(
            "⏭️ Already processed post %s",
            post_id
        )
        return

    logger.info(
        "🔥 NEW X POST DETECTED: %s",
        post_id
    )

    post_text = data.get("text", "")

    video_url = find_best_video(event)

    if not video_url:

        logger.info(
            "ℹ️ Post %s has no video. Skipping.",
            post_id
        )

        # Mark it seen so we don't repeatedly process it
        SEEN_POSTS.add(post_id)
        save_seen_posts()

        return

    logger.info(
        "🎥 Video found for post %s",
        post_id
    )

    video_path = await download_video(
        video_url,
        post_id
    )

    if not video_path:
        return

    caption = (
        f"{post_text}\n\n"
        f"Source: https://x.com/{X_USERNAME}/status/{post_id}"
    )

    success = await send_video_to_channel(
        application,
        video_path,
        post_id,
        caption
    )

    if success:

        SEEN_POSTS.add(post_id)
        save_seen_posts()

        try:
            video_path.unlink()
        except Exception:
            pass


# =========================
# X FILTERED STREAM
# =========================

async def x_stream(application):

    logger.info("🚀 Starting X real-time stream...")

    rule_ready = await ensure_stream_rule()

    if not rule_ready:

        logger.error(
            "❌ X stream rule could not be configured."
        )

        return

    stream_params = {
        "tweet.fields": "id,text,author_id,created_at,attachments",
        "expansions": "attachments.media_keys",
        "media.fields": (
            "media_key,"
            "type,"
            "url,"
            "variants,"
            "duration_ms,"
            "height,"
            "width"
        )
    }

    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=30,
        sock_read=None
    )

    while True:

        try:

            logger.info(
                "🔌 Connecting to X Filtered Stream..."
            )

            async with aiohttp.ClientSession(
                headers=x_headers(),
                timeout=timeout
            ) as session:

                async with session.get(
                    X_STREAM_URL,
                    params=stream_params
                ) as response:

                    if response.status != 200:

                        text = await response.text()

                        logger.error(
                            "❌ X stream connection failed: HTTP %s",
                            response.status
                        )

                        logger.error(text)

                        # Don't hammer X
                        await asyncio.sleep(60)

                        continue

                    logger.info(
                        "🟢 X REAL-TIME STREAM CONNECTED"
                    )

                    async for raw_line in response.content:

                        line = raw_line.decode(
                            "utf-8",
                            errors="ignore"
                        ).strip()

                        if not line:
                            continue

                        # Ignore SSE-style comments
                        if line.startswith(":"):
                            continue

                        # Handle SSE-style data:
                        if line.startswith("data:"):
                            line = line[5:].strip()

                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:

                            logger.warning(
                                "⚠️ Could not parse stream event: %s",
                                line[:300]
                            )

                            continue

                        await process_x_post(
                            application,
                            event
                        )

        except asyncio.CancelledError:
            raise

        except Exception as e:

            logger.exception(
                "❌ X stream disconnected: %s",
                e
            )

            logger.info(
                "🔄 Reconnecting in 15 seconds..."
            )

            await asyncio.sleep(15)


# =========================
# TELEGRAM COMMANDS
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🟢 BiggTims X Video Bot is online.\n\n"
        "I'm watching @FERNANDEZFrdric for new videos."
    )


# =========================
# STARTUP
# =========================

async def post_init(application):

    logger.info(
        "🤖 Telegram bot starting..."
    )

    application.create_task(
        x_stream(application)
    )


# =========================
# MAIN
# =========================

def main():

    logger.info(
        "🚀 BiggTims X → Telegram Video Bot"
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.run_polling()


if __name__ == "__main__":
    main()
