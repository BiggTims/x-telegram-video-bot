import os
import json
import asyncio
import tempfile
import shutil
from pathlib import Path

import aiohttp
import imageio_ffmpeg
import yt_dlp

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")

TELEGRAM_CHANNEL = "@biggtimsxvid"
X_USERNAME = "FERNANDEZFrdric"

CHECK_INTERVAL = 30

STATE_FILE = Path("x_state.json")

# Telegram Bot API upload limit
MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024


# ============================================================
# ENVIRONMENT CHECK
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is missing."
    )

if not X_BEARER_TOKEN:
    raise RuntimeError(
        "X_BEARER_TOKEN environment variable is missing."
    )


# ============================================================
# STATE
# ============================================================

def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except Exception as error:
        print(
            f"State file could not be read: {error}",
            flush=True
        )
        return {}


def save_state(state):
    try:
        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                state,
                file
            )

    except Exception as error:
        print(
            f"State file could not be saved: {error}",
            flush=True
        )


# ============================================================
# X API
# ============================================================

async def x_api_get(endpoint, params=None):
    url = f"https://api.x.com/2/{endpoint}"

    headers = {
        "Authorization": f"Bearer {X_BEARER_TOKEN}",
        "User-Agent": "X-Telegram-Video-Bot/1.0",
    }

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            url,
            headers=headers,
            params=params
        ) as response:

            response_text = await response.text()

            print(
                f"X API → HTTP {response.status} → {endpoint}",
                flush=True
            )

            if response.status != 200:
                print(
                    "X API error response:",
                    flush=True
                )

                print(
                    response_text,
                    flush=True
                )

                raise RuntimeError(
                    f"X API returned HTTP {response.status}"
                )

            try:
                return json.loads(
                    response_text
                )

            except json.JSONDecodeError:
                raise RuntimeError(
                    "X API returned invalid JSON."
                )


# ============================================================
# GET X USER ID
# ============================================================

async def get_x_user_id():

    print(
        f"Looking up X user: @{X_USERNAME}",
        flush=True
    )

    endpoint = (
        f"users/by/username/{X_USERNAME}"
    )

    params = {
        "user.fields": "id,name,username"
    }

    data = await x_api_get(
        endpoint,
        params
    )

    user = data.get("data")

    if not user:
        raise RuntimeError(
            f"Could not find X user @{X_USERNAME}"
        )

    print(
        f"X user found: @{user.get('username')}",
        flush=True
    )

    print(
        f"X user ID: {user.get('id')}",
        flush=True
    )

    return user["id"]


# ============================================================
# GET X POSTS
# ============================================================

async def get_x_posts(
    user_id,
    since_id=None
):

    params = {
        "max_results": 10,

        "tweet.fields": (
            "id,text,created_at,attachments"
        ),

        "expansions": (
            "attachments.media_keys"
        ),

        "media.fields": (
            "media_key,type,url,"
            "preview_image_url,duration_ms,"
            "width,height"
        ),

        "exclude": "retweets,replies",
    }

    if since_id:
        params["since_id"] = since_id

        print(
            f"Checking for posts newer than {since_id}",
            flush=True
        )

    else:
        print(
            "Getting latest X posts...",
            flush=True
        )

    data = await x_api_get(
        f"users/{user_id}/tweets",
        params
    )

    posts = data.get(
        "data",
        []
    )

    includes = data.get(
        "includes",
        {}
    )

    media_items = includes.get(
        "media",
        []
    )

    media_by_key = {
        media.get("media_key"): media
        for media in media_items
    }

    results = []

    for post in posts:

        attachments = post.get(
            "attachments",
            {}
        )

        media_keys = attachments.get(
            "media_keys",
            []
        )

        post_media = []

        for media_key in media_keys:

            if media_key in media_by_key:
                post_media.append(
                    media_by_key[media_key]
                )

        has_video = any(
            media.get("type") == "video"
            for media in post_media
        )

        post_id = post["id"]

        results.append(
            {
                "id": post_id,

                "text": post.get(
                    "text",
                    ""
                ),

                "created_at": post.get(
                    "created_at"
                ),

                "url": (
                    f"https://x.com/"
                    f"{X_USERNAME}/status/"
                    f"{post_id}"
                ),

                "has_video": has_video,

                "media": post_media,
            }
        )

    # Process oldest first
    results.sort(
        key=lambda post: int(
            post["id"]
        )
    )

    return results


# ============================================================
# DOWNLOAD X VIDEO
# ============================================================

async def download_video(post_url):

    temp_dir = tempfile.mkdtemp(
        prefix="xvideo_"
    )

    output_template = os.path.join(
        temp_dir,
        "%(id)s.%(ext)s"
    )

    ffmpeg_path = (
        imageio_ffmpeg.get_ffmpeg_exe()
    )

    ydl_opts = {
        "format": (
            "bestvideo+bestaudio/"
            "best"
        ),

        "outtmpl": output_template,

        "merge_output_format": "mp4",

        "ffmpeg_location": ffmpeg_path,

        "quiet": True,

        "no_warnings": True,

        "noplaylist": True,

        "extractor_args": {
            "twitter": {
                "api": [
                    "graphql"
                ]
            }
        },
    }

    print(
        f"Downloading X video: {post_url}",
        flush=True
    )

    try:

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                post_url,
                download=True
            )

            # First look at requested downloads
            requested_downloads = (
                info.get(
                    "requested_downloads"
                )
                or []
            )

            for download_info in requested_downloads:

                filepath = download_info.get(
                    "filepath"
                )

                if filepath and os.path.exists(
                    filepath
                ):
                    print(
                        f"Downloaded: {filepath}",
                        flush=True
                    )

                    return (
                        filepath,
                        temp_dir
                    )

            # Try prepared filename
            prepared_filename = (
                ydl.prepare_filename(info)
            )

            possible_files = [
                prepared_filename,

                os.path.splitext(
                    prepared_filename
                )[0] + ".mp4",
            ]

            for filepath in possible_files:

                if os.path.exists(filepath):

                    print(
                        f"Downloaded: {filepath}",
                        flush=True
                    )

                    return (
                        filepath,
                        temp_dir
                    )

            # Last resort: inspect temp directory
            for filename in os.listdir(
                temp_dir
            ):

                filepath = os.path.join(
                    temp_dir,
                    filename
                )

                if os.path.isfile(filepath):

                    print(
                        f"Downloaded: {filepath}",
                        flush=True
                    )

                    return (
                        filepath,
                        temp_dir
                    )

            raise RuntimeError(
                "Video downloaded but "
                "output file could not be found."
            )

    except Exception:

        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        raise


# ============================================================
# SEND VIDEO TO TELEGRAM CHANNEL
# ============================================================

async def send_video_to_channel(
    application,
    video_path,
    post
):

    file_size = os.path.getsize(
        video_path
    )

    file_size_mb = (
        file_size / (1024 * 1024)
    )

    print(
        f"Video size: {file_size_mb:.2f} MB",
        flush=True
    )

    if file_size > MAX_TELEGRAM_FILE_SIZE:

        print(
            "❌ Video exceeds Telegram's "
            "50 MB Bot API limit.",
            flush=True
        )

        return False

    caption = (
        "🎥 New video from X\n\n"
        f"🔗 {post['url']}"
    )

    print(
        "Uploading video to Telegram...",
        flush=True
    )

    with open(
        video_path,
        "rb"
    ) as video_file:

        await application.bot.send_video(
            chat_id=TELEGRAM_CHANNEL,
            video=video_file,
            caption=caption,
            supports_streaming=True,
        )

    print(
        "✅ Video successfully posted "
        "to Telegram.",
        flush=True
    )

    return True


# ============================================================
# PROCESS X POST
# ============================================================

async def process_x_post(
    application,
    post
):

    print(
        "--------------------------------------",
        flush=True
    )

    print(
        "NEW X POST DETECTED",
        flush=True
    )

    print(
        f"URL: {post['url']}",
        flush=True
    )

    print(
        f"Contains video: {post['has_video']}",
        flush=True
    )

    if not post["has_video"]:

        print(
            "No video found. Skipping.",
            flush=True
        )

        return True

    video_path = None
    temp_dir = None

    try:

        video_path, temp_dir = (
            await download_video(
                post["url"]
            )
        )

        success = await send_video_to_channel(
            application,
            video_path,
            post
        )

        return success

    except Exception as error:

        print(
            "❌ VIDEO PROCESSING ERROR",
            flush=True
        )

        print(
            repr(error),
            flush=True
        )

        return False

    finally:

        if temp_dir:

            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )


# ============================================================
# X MONITOR
# ============================================================

async def monitor_x(application):

    print(
        "",
        flush=True
    )

    print(
        "======================================",
        flush=True
    )

    print(
        "OFFICIAL X API MONITOR STARTED",
        flush=True
    )

    print(
        f"Monitoring: @{X_USERNAME}",
        flush=True
    )

    print(
        f"Check interval: {CHECK_INTERVAL} seconds",
        flush=True
    )

    print(
        "======================================",
        flush=True
    )

    # --------------------------------------------------------
    # GET USER ID
    # --------------------------------------------------------

    user_id = None

    while user_id is None:

        try:

            user_id = await get_x_user_id()

        except Exception as error:

            print(
                "❌ X USER LOOKUP FAILED",
                flush=True
            )

            print(
                repr(error),
                flush=True
            )

            print(
                f"Retrying in {CHECK_INTERVAL} seconds...",
                flush=True
            )

            await asyncio.sleep(
                CHECK_INTERVAL
            )

    # --------------------------------------------------------
    # LOAD STATE
    # --------------------------------------------------------

    state = load_state()

    last_seen_id = state.get(
        "last_seen_id"
    )

    # --------------------------------------------------------
    # FIRST START
    # --------------------------------------------------------

    if not last_seen_id:

        print(
            "No previous X position found.",
            flush=True
        )

        try:

            latest_posts = await get_x_posts(
                user_id
            )

            if latest_posts:

                latest_id = latest_posts[-1]["id"]

                last_seen_id = latest_id

                save_state(
                    {
                        "last_seen_id":
                        last_seen_id
                    }
                )

                print(
                    f"Initial position set: "
                    f"{last_seen_id}",
                    flush=True
                )

                print(
                    "Existing posts will not "
                    "be downloaded.",
                    flush=True
                )

            else:

                print(
                    "X returned no posts.",
                    flush=True
                )

        except Exception as error:

            print(
                "❌ Initial X API check failed:",
                flush=True
            )

            print(
                repr(error),
                flush=True
            )

    # --------------------------------------------------------
    # CONTINUOUS MONITOR
    # --------------------------------------------------------

    while True:

        try:

            posts = await get_x_posts(
                user_id,
                since_id=last_seen_id
            )

            if posts:

                print(
                    f"Found {len(posts)} new "
                    f"X post(s).",
                    flush=True
                )

                for post in posts:

                    post_id = post["id"]

                    print(
                        f"Processing post {post_id}",
                        flush=True
                    )

                    # Save position
                    last_seen_id = post_id

                    save_state(
                        {
                            "last_seen_id":
                            last_seen_id
                        }
                    )

                    await process_x_post(
                        application,
                        post
                    )

            else:

                print(
                    "No new X posts.",
                    flush=True
                )

        except Exception as error:

            print(
                "======================================",
                flush=True
            )

            print(
                "X MONITOR ERROR",
                flush=True
            )

            print(
                repr(error),
                flush=True
            )

            print(
                "======================================",
                flush=True
            )

        # ----------------------------------------------------
        # IMPORTANT:
        # ALWAYS wait before the next X API request.
        # ----------------------------------------------------

        print(
            f"Next X check in {CHECK_INTERVAL} seconds...",
            flush=True
        )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 X Video Saver is online!\n\n"
        "Manual:\n"
        "/save X_POST_URL\n\n"
        "Automatic monitoring:\n"
        f"@{X_USERNAME}"
    )


# ============================================================
# /SAVE
# ============================================================

async def save_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.message.reply_text(
            "Usage:\n"
            "/save X_POST_URL"
        )

        return

    post_url = context.args[0]

    status_message = await update.message.reply_text(
        "⏳ Downloading video..."
    )

    video_path = None
    temp_dir = None

    try:

        video_path, temp_dir = (
            await download_video(
                post_url
            )
        )

        file_size = os.path.getsize(
            video_path
        )

        if file_size > MAX_TELEGRAM_FILE_SIZE:

            await status_message.edit_text(
                "❌ Video is larger than "
                "Telegram's 50 MB limit."
            )

            return

        await status_message.edit_text(
            "📤 Uploading video..."
        )

        with open(
            video_path,
            "rb"
        ) as video_file:

            await update.message.reply_video(
                video=video_file,
                supports_streaming=True
            )

        await status_message.edit_text(
            "✅ Video saved successfully."
        )

    except Exception as error:

        print(
            "❌ MANUAL /save ERROR",
            flush=True
        )

        print(
            repr(error),
            flush=True
        )

        try:

            await status_message.edit_text(
                f"❌ Failed to save video.\n\n"
                f"{error}"
            )

        except Exception:
            pass

    finally:

        if temp_dir:

            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )


# ============================================================
# POST INIT
# ============================================================

async def post_init(application):

    print(
        "======================================",
        flush=True
    )

    print(
        "X → Telegram Video Bot is running...",
        flush=True
    )

    print(
        "Telegram bot initialized.",
        flush=True
    )

    print(
        "Starting X API monitor...",
        flush=True
    )

    print(
        "======================================",
        flush=True
    )

    asyncio.create_task(
        monitor_x(application)
    )


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "save",
            save_command
        )
    )

    print(
        "Starting Telegram polling...",
        flush=True
    )

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
