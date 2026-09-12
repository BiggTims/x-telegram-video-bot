import os
import asyncio
import tempfile
import json
import re
import time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import yt_dlp
import imageio_ffmpeg
import feedparser


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

CHANNEL = "@biggtimsxvid"

# X account we want to monitor
X_USERNAME = "FERNANDEZFrdric"

# Check for new posts every 60 seconds
CHECK_INTERVAL = 60

# RSS feed used to detect new posts
RSS_URL = (
    f"https://rsshub.isrss.com/twitter/user/"
    f"{X_USERNAME}?routeParams=exclude_rts_replies"
)

# File used to remember processed posts
STATE_FILE = "processed_posts.json"


# ============================================================
# STATE / DUPLICATE PROTECTION
# ============================================================

def load_processed_posts():

    if not os.path.exists(STATE_FILE):
        return set()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        return set(data)

    except Exception as error:

        print(
            "Could not load processed posts:",
            error
        )

        return set()


def save_processed_posts(processed_posts):

    # Keep only the latest 500 IDs
    posts = list(processed_posts)[-500:]

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                posts,
                file
            )

    except Exception as error:

        print(
            "Could not save processed posts:",
            error
        )


# ============================================================
# DOWNLOAD VIDEO FROM X
# ============================================================

def download_video(
    url: str,
    output_dir: str
):

    output_template = os.path.join(
        output_dir,
        "%(id)s.%(ext)s"
    )

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    options = {

        "outtmpl": output_template,

        "format": (
            "bestvideo+bestaudio/"
            "best"
        ),

        "merge_output_format": "mp4",

        "ffmpeg_location": ffmpeg_path,

        "noplaylist": True,

        "extractor_args": {
            "twitter": {
                "api": ["graphql"]
            }
        },

        "quiet": False,

        "no_warnings": False,
    }

    with yt_dlp.YoutubeDL(options) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        filename = ydl.prepare_filename(
            info
        )

        # Expected merged MP4
        mp4_file = (
            os.path.splitext(filename)[0]
            + ".mp4"
        )

        if os.path.exists(mp4_file):
            return mp4_file

        # Fallback
        if os.path.exists(filename):
            return filename

        # Search temporary directory
        for file in os.listdir(output_dir):

            path = os.path.join(
                output_dir,
                file
            )

            if os.path.isfile(path):

                if file.lower().endswith(
                    (
                        ".mp4",
                        ".mkv",
                        ".webm",
                        ".mov"
                    )
                ):

                    return path

        raise Exception(
            "Downloaded video file could not be found."
        )


# ============================================================
# EXTRACT X POST ID
# ============================================================

def extract_post_id(url):

    if not url:
        return None

    match = re.search(
        r"/status/(\d+)",
        url
    )

    if match:
        return match.group(1)

    return None


# ============================================================
# GET POSTS FROM RSS FEED
# ============================================================

def get_x_posts():

    print(
        "Checking X:",
        X_USERNAME
    )

    feed = feedparser.parse(
        RSS_URL
    )

    if feed.bozo and not feed.entries:

        raise Exception(
            "RSS feed could not be read."
        )

    posts = []

    for entry in feed.entries:

        url = (
            getattr(
                entry,
                "link",
                None
            )
            or ""
        )

        post_id = extract_post_id(
            url
        )

        if not post_id:
            continue

        title = (
            getattr(
                entry,
                "title",
                ""
            )
            or ""
        )

        published = (
            getattr(
                entry,
                "published_parsed",
                None
            )
        )

        if published:

            timestamp = time.mktime(
                published
            )

        else:

            timestamp = 0

        posts.append(
            {
                "id": post_id,
                "url": url,
                "title": title,
                "timestamp": timestamp,
            }
        )

    # Oldest first
    posts.sort(
        key=lambda item: item["timestamp"]
    )

    return posts


# ============================================================
# SEND X VIDEO TO TELEGRAM
# ============================================================

async def process_x_post(
    application,
    post
):

    post_id = post["id"]

    url = post["url"]

    print(
        "New X post detected:",
        url
    )

    try:

        with tempfile.TemporaryDirectory() as temp_dir:

            video_path = await asyncio.to_thread(
                download_video,
                url,
                temp_dir
            )

            if not os.path.exists(
                video_path
            ):

                raise Exception(
                    "Video file was not created."
                )

            file_size = os.path.getsize(
                video_path
            )

            # Telegram standard Bot API limit
            if file_size > 50 * 1024 * 1024:

                print(
                    "Video is larger than 50 MB:",
                    url
                )

                return False

            caption = (
                f"📥 @{X_USERNAME}\n\n"
                f"🔗 {url}"
            )

            print(
                "Uploading video to Telegram..."
            )

            with open(
                video_path,
                "rb"
            ) as video:

                await application.bot.send_video(
                    chat_id=CHANNEL,
                    video=video,
                    caption=caption,
                    supports_streaming=True
                )

            print(
                "✅ Successfully posted:",
                post_id
            )

            return True

    except Exception as error:

        error_text = str(error)

        # A normal X post without a video
        # should simply be skipped.
        no_video_messages = [
            "does not have a video",
            "No video formats found",
            "No video could be found",
            "Unsupported URL",
        ]

        if any(
            message.lower()
            in error_text.lower()
            for message in no_video_messages
        ):

            print(
                "No downloadable video:",
                url
            )

            return True

        print(
            "ERROR processing:",
            url
        )

        print(
            error
        )

        return False


# ============================================================
# AUTOMATIC X MONITOR
# ============================================================

async def monitor_x(
    application
):

    print(
        "======================================"
    )

    print(
        "🤖 X MONITOR STARTED"
    )

    print(
        f"Monitoring @{X_USERNAME}"
    )

    print(
        f"Checking every {CHECK_INTERVAL} seconds"
    )

    print(
        "======================================"
    )

    processed_posts = load_processed_posts()

    first_check = True

    while True:

        try:

            posts = await asyncio.to_thread(
                get_x_posts
            )

            if not posts:

                print(
                    "No posts found in RSS feed."
                )

            else:

                # On first startup, don't download
                # old posts. We only establish the
                # current position.
                if first_check:

                    for post in posts:

                        processed_posts.add(
                            post["id"]
                        )

                    save_processed_posts(
                        processed_posts
                    )

                    print(
                        f"Initial position set. "
                        f"Remembering {len(posts)} posts."
                    )

                    first_check = False

                else:

                    for post in posts:

                        post_id = post["id"]

                        if post_id in processed_posts:
                            continue

                        print(
                            "--------------------------------------"
                        )

                        print(
                            "🆕 NEW POST:",
                            post["url"]
                        )

                        success = await process_x_post(
                            application,
                            post
                        )

                        # Mark as processed whether:
                        # - video was successfully uploaded
                        # - post had no video
                        #
                        # If an unexpected download error
                        # occurs, don't mark it yet.
                        if success:

                            processed_posts.add(
                                post_id
                            )

                            save_processed_posts(
                                processed_posts
                            )

        except Exception as error:

            print(
                "MONITOR ERROR:",
                error
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    await update.message.reply_text(
        "🤖 X Video Saver is online!\n\n"
        "I can save X videos manually with:\n\n"
        "/save X_POST_URL\n\n"
        f"👀 Automatic monitoring:\n"
        f"@{X_USERNAME}"
    )


# ============================================================
# /SAVE
# ============================================================

async def save_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not context.args:

        await update.message.reply_text(
            "Send me an X post URL after /save.\n\n"
            "Example:\n"
            "/save https://x.com/user/status/123456789"
        )

        return

    url = context.args[0]

    if (
        "x.com/" not in url
        and "twitter.com/" not in url
    ):

        await update.message.reply_text(
            "❌ That doesn't look like an X/Twitter post URL."
        )

        return

    status = await update.message.reply_text(
        "⏳ Downloading the video from X..."
    )

    try:

        with tempfile.TemporaryDirectory() as temp_dir:

            video_path = await asyncio.to_thread(
                download_video,
                url,
                temp_dir
            )

            if not os.path.exists(
                video_path
            ):

                raise Exception(
                    "Video file was not created."
                )

            file_size = os.path.getsize(
                video_path
            )

            if file_size > 50 * 1024 * 1024:

                await status.edit_text(
                    "❌ This video is larger than "
                    "Telegram's 50 MB bot upload limit."
                )

                return

            await status.edit_text(
                "📤 Video downloaded!\n"
                "Uploading to @biggtimsxvid..."
            )

            with open(
                video_path,
                "rb"
            ) as video:

                await context.bot.send_video(
                    chat_id=CHANNEL,
                    video=video,
                    caption=(
                        f"📥 Saved from X\n"
                        f"🔗 {url}"
                    ),
                    supports_streaming=True
                )

            try:

                await status.edit_text(
                    "✅ Video saved successfully "
                    "to @biggtimsxvid!"
                )

            except Exception as status_error:

                print(
                    "STATUS UPDATE ERROR:",
                    status_error
                )

    except Exception as error:

        print(
            "MANUAL DOWNLOAD ERROR:"
        )

        print(error)

        try:

            await status.edit_text(
                "❌ I couldn't download that X video."
            )

        except Exception:

            pass


# ============================================================
# STARTUP
# ============================================================

async def post_init(
    application
):

    # Start automatic monitoring
    application.create_task(
        monitor_x(
            application
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Manual commands
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "save",
            save_video
        )
    )

    print(
        "🤖 X → Telegram Video Bot is running..."
    )

    app.run_polling()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
