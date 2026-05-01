

import os
import asyncio
from yt_dlp import YoutubeDL
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ================= CONFIG =================
BOT_TOKEN = "___"   # 🔹 Put your bot token here
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ==========================================

# Progress Hook (runs in yt_dlp thread)
def progress_hook(loop, message):
    def inner(d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '').strip()
            speed = d.get('_speed_str', '0 KiB/s')
            eta = d.get('eta', 0)

            text = f"📥 Downloading...\nProgress: {percent}\nSpeed: {speed}\nETA: {eta}s"
            asyncio.run_coroutine_threadsafe(message.edit_text(text), loop)

        elif d['status'] == 'finished':
            text = "✅ Download finished, preparing file..."
            asyncio.run_coroutine_threadsafe(message.edit_text(text), loop)

    return inner


# Download function
async def download_video(url, quality, message, loop):
    ydl_opts = {
        "format": f"bestvideo[height<={quality}]+bestaudio/best",
        "progress_hooks": [progress_hook(loop, message)],
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        "merge_output_format": "mp4"
    }

    def run_ydl():
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            if not filepath.endswith(".mp4"):
                filepath = os.path.splitext(filepath)[0] + ".mp4"
            return info, filepath

    return await loop.run_in_executor(None, run_ydl)


# Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send me a YouTube link to download.")


# Handle YouTube link
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Please send a valid YouTube link.")
        return

    # Quality selection buttons
    keyboard = [
        [InlineKeyboardButton("360p", callback_data=f"{url}|360"),
         InlineKeyboardButton("480p", callback_data=f"{url}|480")],
        [InlineKeyboardButton("720p", callback_data=f"{url}|720"),
         InlineKeyboardButton("1080p", callback_data=f"{url}|1080")],
        [InlineKeyboardButton("Best Quality", callback_data=f"{url}|2160")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🎥 Choose download quality:", reply_markup=reply_markup)


# Handle Quality Selection
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    url, quality = query.data.split("|")
    msg = await query.edit_message_text("🔄 Starting download...")

    loop = asyncio.get_running_loop()

    try:
        info, filepath = await download_video(url, quality, msg, loop)

        await msg.edit_text("📤 Uploading file...")

        # Send file
        with open(filepath, "rb") as f:
            await query.message.reply_document(f, filename=os.path.basename(filepath))

        await msg.edit_text("✅ Done!")

        # Clean up
        os.remove(filepath)

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")


# Main
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button))

    print("🤖 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
