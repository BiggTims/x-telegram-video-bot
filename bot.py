import os
import json
import asyncio
import tempfile
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

MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024


# ============================================================
# BASIC VALIDATION
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

if not X_BEARER_TOKEN:
    raise RuntimeError("X_BEARER_TOKEN environment variable is missing.")


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as error:
        print(
            f"Could not read state file: {error}",
            flush=True
        )
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as file:
            json.dump(state, file)
    except Exception as error:
        print(
            f"Could not save state file: {error}",
            flush=True
        )


# ============================================================
# X API REQUEST
# ============================================================

async def x_api_get(endpoint, params=None):
    url = f"https://api.x.com/2/{endpoint}"

    headers = {
        "Authorization": f"Bearer {X_BEARER_TOKEN}",
        "User-Agent": "X-Telegram-Video-Bot/1.0",
    }

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.get(
            url,
            headers=headers,
            params=params,
        ) as response:

            text = await response.text()

            print(
                f"X API → {response.status} {endpoint}",
                flush=True
            )

            if response.status != 200:
                print(
                    "X API ERROR RESPONSE:",
                    flush=True
                )

                print(
                    text,
                    flush=True
                )

                raise RuntimeError(
                    f"X API returned HTTP {response.status}"
                )

            try:
                return json.loads(text)

            except json.JSONDecodeError:
                raise RuntimeError(
                    "X API returned invalid JSON."
                )


# ============================================================
# FIND X USER ID
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
        "user.fields": "id,name,username",
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
# GET POSTS FROM X
# ============================================================

async def get_x_posts(
    user_id,
    since_id=None
):

    params = {
        "max_results": 10,

        "tweet.fields": (
            "id,text,created_at,"
            "attachments"
        ),

        "expansions": (
            "attachments.media_keys"
        ),

        "media.fields": (
            "media_key,type,"
            "url,preview_image_url,"
            "duration_ms,width,height"
        ),

        # We only want original posts.
        "exclude": "retweets,replies",
    }

    if since_id:
        params["since_id"] = since_id

        print(
            f"Checking X posts newer than {since_id}",
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

    posts = data.get("data", [])

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

        post_media = [
            media_by_key[key]
            for key in media_keys
            if key in media_by_key
        ]

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

    # X normally returns newest first.
    # We process oldest first.
    results.sort(
        key=lambda item: int(item["id"])
    )

    return results


# ============================================================
# DOWNLOAD VIDEO FROM X
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
        imageio_ffmpeg
        .get_ffmpeg_exe()
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
        f"Downloading video: {post_url}",
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

            requested_downloads = (
                info.get(
                    "requested_downloads"
                )
                or []
            )

            downloaded_file = None

            for item in requested_downloads:

                filepath = item.get(
                    "filepath"
                )

                if filepath and os.path.exists(
                    filepath
                ):
                    downloaded_file = filepath
                    break

            if not downloaded_file:

                prepared = ydl.prepare_filename(
                    info
                )

                possible_files = [
                    prepared,
                    os.path.splitext(
                        prepared
                    )[0] + ".mp4",
                ]

                for filepath in possible_files:

                    if os.path.exists(filepath):
                        downloaded_file = filepath
                        break

            if not downloaded_file:

                for filename in os.listdir(
                    temp_dir
                ):

                    filepath = os.path.join(
                        temp_dir,
