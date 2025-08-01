import logging
import requests
import json
import time
import threading
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    MessageHandler, filters
)
import asyncio

# ─── CONFIG ─────────────────────────────────────────────────────────────────────
DATA_FILE = 'monitor_list.json'
CHECK_INTERVAL = 300  # 5 minutes
DAILY_UPDATE_HOUR_UTC = 8  # 8 AM UTC
# ────────────────────────────────────────────────────────────────────────────────

# ─── LOGGING ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
# ────────────────────────────────────────────────────────────────────────────────

# ─── DATA STORAGE ────────────────────────────────────────────────────────────────
def load_data():
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

monitor_list = load_data()
# ────────────────────────────────────────────────────────────────────────────────

# ─── HELPERS ─────────────────────────────────────────────────────────────────────
def check_instagram_username(username: str) -> bool:
    url = f"https://www.instagram.com/{username}/"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    return response.status_code == 200

def format_duration(td):
    seconds = int(td.total_seconds())
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return ' '.join(parts) or 'less than a minute'

def get_user_monitored(chat_id):
    return {
        uname: info for uname, info in monitor_list.items()
        if info["chat_id"] == chat_id and info["status"] == "monitoring"
    }
# ────────────────────────────────────────────────────────────────────────────────

# ─── BACKGROUND MONITORING ───────────────────────────────────────────────────────
def monitor_accounts(bot_app):
    while True:
        now = datetime.utcnow()
        updated = False

        for username, info in list(monitor_list.items()):
            if info.get("status") == "unbanned":
                continue

            exists = check_instagram_username(username)

            if exists:
                start_time = datetime.fromisoformat(info["start_time"])
                elapsed = now - start_time
                duration_str = format_duration(elapsed)

                message = (f"✅ Instagram account @{username} has been UNBANNED!\n"
                           f"⏱️ Time taken: {duration_str}")

                try:
                    asyncio.run(bot_app.bot.send_message(chat_id=info["chat_id"], text=message))
                except Exception as e:
                    logger.error(f"Failed to notify for @{username}: {e}")

                monitor_list[username]["status"] = "unbanned"
                monitor_list[username]["unban_time"] = now.isoformat()
                updated = True

        if updated:
            save_data(monitor_list)

        time.sleep(CHECK_INTERVAL)
# ────────────────────────────────────────────────────────────────────────────────

# ─── DAILY SUMMARY ───────────────────────────────────────────────────────────────
def send_daily_summary(bot_app):
    while True:
        now = datetime.utcnow()
        target_time = now.replace(hour=DAILY_UPDATE_HOUR_UTC, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        time.sleep(wait_seconds)

        users = set(info["chat_id"] for info in monitor_list.values())

        for chat_id in users:
            entries = get_user_monitored(chat_id)
            if not entries:
                continue

            lines = ["📊 *Daily Monitoring Summary:*", ""]
            for uname, info in entries.items():
                start_time = datetime.fromisoformat(info["start_time"])
                duration = datetime.utcnow() - start_time
                lines.append(f"🔍 @{uname} — monitoring for {format_duration(duration)}")

            message = "\n".join(lines)
            try:
                asyncio.run(bot_app.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown"))
            except Exception as e:
                logger.error(f"Daily update failed for {chat_id}: {e}")
# ────────────────────────────────────────────────────────────────────────────────

# ─── BOT HANDLERS ────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Send me *Instagram usernames* (one or more, space or line-separated) to monitor for unbanning.\n"
        "Use /status to check what you're monitoring.\n"
        "Use /remove <username> to stop monitoring a username.",
        parse_mode="Markdown"
    )

async def handle_usernames(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.message.chat_id

    raw_usernames = set(text.split())  # Split by whitespace
    response_lines = []

    for username in raw_usernames:
        username = username.lower().strip()

        if not username.replace('.', '').replace('_', '').isalnum():
            response_lines.append(f"⚠️ Invalid: @{username}")
            continue

        if username in monitor_list and monitor_list[username]["chat_id"] == chat_id:
            response_lines.append(f"⏳ Already monitoring @{username}")
            continue

        exists = check_instagram_username(username)

        if exists:
            response_lines.append(f"✅ @{username} is already active/unbanned.")
        else:
            monitor_list[username] = {
                "start_time": datetime.utcnow().isoformat(),
                "status": "monitoring",
                "chat_id": chat_id
            }
            response_lines.append(f"🔍 Started monitoring @{username}.")

    save_data(monitor_list)
    await update.message.reply_text("\n".join(response_lines))

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    entries = get_user_monitored(chat_id)

    if not entries:
        await update.message.reply_text("📭 You're not monitoring any usernames.")
        return

    lines = ["📌 You're currently monitoring:"]
    for uname, info in entries.items():
        start_time = datetime.fromisoformat(info["start_time"])
        duration = format_duration(datetime.utcnow() - start_time)
        lines.append(f"• @{uname} — {duration}")

    await update.message.reply_text("\n".join(lines))

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    chat_id = update.message.chat_id

    if not args:
        await update.message.reply_text("❗ Usage: /remove <username>")
        return

    username = args[0].lower()

    if username in monitor_list and monitor_list[username]["chat_id"] == chat_id:
        del monitor_list[username]
        save_data(monitor_list)
        await update.message.reply_text(f"🗑️ Stopped monitoring @{username}.")
    else:
        await update.message.reply_text(f"⚠️ You're not monitoring @{username}.")
# ────────────────────────────────────────────────────────────────────────────────

# ─── MAIN ────────────────────────────────────────────────────────────────────────
def main():
    TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_usernames))

    threading.Thread(target=monitor_accounts, args=(app,), daemon=True).start()
    threading.Thread(target=send_daily_summary, args=(app,), daemon=True).start()

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
