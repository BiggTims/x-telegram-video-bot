import os
import asyncio
import tempfile

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import yt_dlp
import imageio_ffmpeg


# =========================
# CONFIGURATION
# =========================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = "@biggtimsxvid"


# =========================
# DOWNLOAD VIDEO FROM X
# =========================

def download_video(url: str, output_dir: str):

    output_template = os.path.join(
        output_dir,
        "%(id)s.%(ext)s"
    )

    # Get FFmpeg installed through imageio-ffmpeg
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    options = {
        "outtmpl": output_template,

        # Best video + best audio
        "format": "bestvideo+bestaudio/best",

        # Merge into MP4
        "merge_output_format": "mp4",

        # Tell yt-dlp where FFmpeg is
        "ffmpeg_location": ffmpeg_path,

        # Only download the requested post
        "noplaylist": True,

        # X/Twitter extractor
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

        filename = ydl.prepare_filename(info)

        # Expected MP4 after merging
        mp4_file = (
            os.path.splitext(filename)[0]
            + ".mp4"
        )

        if os.path.exists(mp4_file):
            return mp4_file

        # If yt-dlp produced another format
        if os.path.exists(filename):
            return filename

        # Search the temporary directory
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


# =========================
# /START COMMAND
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    await update.message.reply_text(
        "🤖 X Video Saver is online!\n\n"
        "Send me an X post link containing a video."
    )


# =========================
# /SAVE COMMAND
# =========================

async def save_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    # No URL provided
    if not context.args:

        await update.message.reply_text(
            "Send me an X post URL after /save.\n\n"
            "Example:\n"
            "/save https://x.com/user/status/123456789"
        )

        return

    url = context.args[0]

    # Make sure it looks like an X/Twitter URL
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

        # Temporary folder for the downloaded video
        with tempfile.TemporaryDirectory() as temp_dir:

            video_path = await asyncio.to_thread(
                download_video,
                url,
                temp_dir
            )

            # Make sure the file exists
            if not os.path.exists(video_path):

                raise Exception(
                    "Video file was not created."
                )

            file_size = os.path.getsize(
                video_path
            )

            # Telegram standard Bot API limit
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

            # Upload video to Telegram channel
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

            # IMPORTANT:
            # Telegram has successfully received
            # the video at this point.

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
            "===================================="
        )

        print(
            "DOWNLOAD/UPLOAD ERROR:"
        )

        print(error)

        print(
            "===================================="
        )

        try:

            await status.edit_text(
                "❌ I couldn't download that X video."
            )

        except Exception:

            pass


# =========================
# MAIN BOT
# =========================

def main():

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # /start
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # /save
    app.add_handler(
        CommandHandler(
            "save",
            save_video
        )
    )

    print(
        "🤖 X Video Saver is running..."
    )

    app.run_polling()


# =========================
# START
# =========================

if __name__ == "__main__":
    main()
