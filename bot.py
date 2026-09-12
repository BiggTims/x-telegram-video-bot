import os
import asyncio
import tempfile
import json
import re

import aiohttp

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import yt_dlp
import imageio_ffmpeg


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]

X_BEARER_TOKEN = os.environ["X_BEARER_TOKEN"]

CHANNEL = "@biggtimsxvid"

X_USERNAME = "FERNANDEZFrdric"

# Check X every 30 seconds
CHECK_INTERVAL = 30

# Remember the latest X post we have seen
STATE_FILE = "x_state.json"

X_API_BASE = "https://api.x.com/2"


# ============================================================
# STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception as error:

        print("Could not load state:", error)

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
                file,
                indent=2
            )

    except Exception as error:

        print("Could not save state:", error)


# ============================================================
# X API REQUEST
# ============================================================

async def x_api_get(
    session,
    endpoint,
    params=None
):

    url = f"{X_API_BASE}{endpoint}"

    headers = {
        "Authorization": f"Bearer {X_BEARER_TOKEN}"
    }

    async with session.get(
        url,
        headers=headers,
        params=params,
        timeout=aiohttp.ClientTimeout(total=30)
    ) as response:

        text = await response.text()

        if response.status != 200:

            raise Exception(
                f"X API error {response.status}: {text}"
            )

        try:

            return json.loads(text)

        except Exception:

            raise Exception(
                f"Invalid X API response: {text}"
            )


# ============================================================
# GET X USER ID
# ============================================================

async def get_x_user_id():

    print(
        f"Looking up X user: @{X_USERNAME}"
    )

    async with aiohttp.ClientSession() as session:

        data = await x_api_get(
            session,
            f"/users/by/username/{X_USERNAME}",
            {
                "user.fields": "id,username"
            }
        )

    user = data.get("data")

    if not user:

        raise Exception(
            f"Could not find X user @{X_USERNAME}"
        )

    user_id = user["id"]

    print(
        f"✅ X user found: @{user['username']}"
    )

    print(
        f"X user ID: {user_id}"
    )

    return user_id


# ============================================================
# GET NEW X POSTS
# ============================================================

async def get_x_posts(
    user_id,
    since_id=None
):

    params = {
        "max_results": 10,
        "tweet.fields": "id,text,created_at,attachments",
        "expansions": "attachments.media_keys",
        "media.fields": "media_key,type"
    }

    if since_id:

        params["since_id"] = since_id

    async with aiohttp.ClientSession() as session:

        data = await x_api_get(
            session,
            f"/users/{user_id}/tweets",
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
        media["media_key"]: media
        for media in media_items
    }

    results = []

    for post in posts:

        media_keys = (
            post.get("attachments", {})
            .get("media_keys", [])
        )

        has_video = False

        for media_key in media_keys:

            media = media_by_key.get(
                media_key
            )

            if not media:
                continue

            if media.get("type") == "video":

                has_video = True
                break

        results.append(
            {
                "id": post["id"],
                "url": (
                    f"https://x.com/"
                    f"{X_USERNAME}/status/"
                    f"{post['id']}"
                ),
                "text": post.get(
                    "text",
                    ""
                ),
                "created_at": post.get(
                    "created_at"
                ),
                "has_video": has_video,
            }
        )

    # X returns newest first.
    # We process oldest first.
    results.sort(
        key=lambda post: int(post["id"])
    )

    return results


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

    ffmpeg_path = (
        imageio_ffmpeg.get_ffmpeg_exe()
    )

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

        mp4_file = (
            os.path.splitext(filename)[0]
            + ".mp4"
        )

        if os.path.exists(mp4_file):

            return mp4_file

        if os.path.exists(filename):

            return filename

        for file in os.listdir(
            output_dir
        ):

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
# PROCESS X VIDEO
# ============================================================

async def process_x_post(
    application,
    post
):

    post_id = post["id"]

    url = post["url"]

    print(
        "======================================"
    )

    print(
        "🎬 VIDEO POST DETECTED"
    )

    print(
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

            if file_size > 50 * 1024 * 1024:

                print(
                    "❌ Video is larger than 50 MB."
                )

                return False

            caption = (
                f"📥 @{X_USERNAME}\n\n"
                f"🔗 {url}"
            )

            print(
                "📤 Uploading to Telegram..."
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
                f"✅ Successfully posted {post_id}"
            )

            return True

    except Exception as error:

        print(
            "❌ ERROR processing video:"
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
        "🤖 OFFICIAL X API MONITOR STARTED"
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

    # Get the X account's numeric ID
    try:

        user_id = await get_x_user_id()

    except Exception as error:

        print(
            "❌ Could not initialize X API:"
        )

        print(
            error
        )

        return

    state = load_state()

    last_seen_id = state.get(
        "last_seen_id"
    )

    # --------------------------------------------------------
    # FIRST STARTUP
    # --------------------------------------------------------

    if not last_seen_id:

        print(
            "No previous X position found."
        )

        print(
            "Getting latest posts..."
        )

        try:

            posts = await get_x_posts(
                user_id
            )

            if posts:

                latest_id = max(
                    post["id"]
                    for post in posts
                )

                save_state(
                    {
                        "last_seen_id": latest_id
                    }
                )

                print(
                    f"✅ Initial position set:"
                    f" {latest_id}"
                )

                print(
                    "Old posts will NOT be downloaded."
                )

            else:

                print(
                    "No posts found."
                )

        except Exception as error:

            print(
                "❌ Initial X API check failed:"
            )

            print(
                error
            )

        # Give X API a moment before monitoring
        await asyncio.sleep(3)

        last_seen_id = load_state().get(
            "last_seen_id"
        )

    # --------------------------------------------------------
    # CONTINUOUS MONITORING
    # --------------------------------------------------------

    while True:

        try:

            posts = await get_x_posts(
                user_id,
                since_id=last_seen_id
            )

            if posts:

                print(
                    f"🆕 Found {len(posts)} new X post(s)."
                )

                for post in posts:

                    post_id = post["id"]

                    # Move our position forward
                    last_seen_id = post_id

                    save_state(
                        {
                            "last_seen_id": last_seen_id
                        }
                    )

                    print(
                        "--------------------------------------"
                    )

                    print(
                        "New X post:"
                    )

                    print(
                        post["url"]
                    )

                    if not post["has_video"]:

                        print(
                            "⏭️ No video. Skipping."
                        )

                        continue

                    success = await process_x_post(
                        application,
                        post
                    )

                    if not success:

                        print(
                            "⚠️ Video could not be uploaded."
                        )

            else:

                print(
                    "No new X posts."
                )

        except Exception as error:

            print(
                "======================================"
            )

            print(
                "❌ X MONITOR ERROR"
            )

            print(
                error
            )

            print(
                "======================================"
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
        "Manual:\n"
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

        print(
            error
        )

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
