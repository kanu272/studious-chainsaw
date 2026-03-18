#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🤖 TELEGRAM BOT — NEW CLEAN VERSION
Features:
  ✅ Welcome Message (on /start)
  ✅ Force Join Channels (admin manage korbe)
  ✅ Mini App Button
  ✅ Broadcast (admin pathale sob user pabe)
  ✅ User Stats (total, active, left — MongoDB e save)
"""

import os
import sys
import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# ===================== ENV CONFIG =====================
BOT_TOKEN  = os.getenv("BOT_TOKEN")
MONGO_URI  = os.getenv("MONGO_URI")
ADMIN_ID   = os.getenv("ADMIN_ID")

# Mini App URL শুধু Admin panel থেকে সেট হবে — env লাগবে না
DEFAULT_MINI_APP_URL = ""

for var, name in [(BOT_TOKEN, "BOT_TOKEN"), (MONGO_URI, "MONGO_URI"), (ADMIN_ID, "ADMIN_ID")]:
    if not var:
        print(f"❌ ERROR: {name} environment variable is not set!")
        sys.exit(1)

try:
    ADMIN_ID = int(ADMIN_ID)
except ValueError:
    print("❌ ERROR: ADMIN_ID must be a number!")
    sys.exit(1)

# ===================== LOGGING =====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ===================== MONGODB =====================
try:
    logger.info("🔄 Connecting to MongoDB...")
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.server_info()
    db = mongo_client["telegram_bot"]

    users_col         = db["users"]           # All users
    channels_col      = db["force_channels"]  # Force join channels
    settings_col      = db["settings"]        # Bot settings
    pending_col       = db["pending_requests"]# Private channel join requests

    # Indexes
    users_col.create_index("user_id", unique=True, background=True)
    users_col.create_index("last_active", background=True)
    channels_col.create_index("channel_id", unique=True, background=True)
    pending_col.create_index([("user_id", 1), ("channel_id", 1)], background=True)

    logger.info("✅ MongoDB Connected!")
except ConnectionFailure as e:
    logger.error(f"❌ MongoDB Failed: {e}")
    sys.exit(1)

# ===================== ADMIN STATE =====================
# Track admins who are in broadcast mode
admin_broadcast_mode = set()

# ===================== SETTINGS HELPERS =====================
def get_setting(key, default=None):
    try:
        doc = settings_col.find_one({"key": key})
        return doc["value"] if doc else default
    except Exception as e:
        logger.error(f"get_setting error: {e}")
        return default

def set_setting(key, value):
    try:
        settings_col.update_one(
            {"key": key},
            {"$set": {"key": key, "value": value, "updated_at": datetime.utcnow()}},
            upsert=True,
        )
    except Exception as e:
        logger.error(f"set_setting error: {e}")

# ===================== USER HELPERS =====================
def save_user(user_id: int, username: str, first_name: str):
    """Save/update user. Mark as active. Track first_seen."""
    try:
        users_col.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "username": username,
                    "first_name": first_name,
                    "last_active": datetime.utcnow(),
                    "is_active": True,
                },
                "$setOnInsert": {"first_seen": datetime.utcnow()},
            },
            upsert=True,
        )
    except Exception as e:
        logger.error(f"save_user error: {e}")

def mark_user_left(user_id: int):
    """Mark user as left/blocked (called when bot can't send message)."""
    try:
        users_col.update_one(
            {"user_id": user_id},
            {"$set": {"is_active": False, "left_at": datetime.utcnow()}},
        )
    except Exception as e:
        logger.error(f"mark_user_left error: {e}")

def get_all_user_ids():
    try:
        return [u["user_id"] for u in users_col.find({}, {"user_id": 1})]
    except Exception as e:
        logger.error(f"get_all_user_ids error: {e}")
        return []

def get_stats():
    """Return dict with total, active_today, active_users, left_users."""
    try:
        yesterday = datetime.utcnow() - timedelta(days=1)
        total       = users_col.count_documents({})
        active_now  = users_col.count_documents({"is_active": True})
        left        = users_col.count_documents({"is_active": False})
        active_24h  = users_col.count_documents({"last_active": {"$gte": yesterday}})
        return {
            "total":      total,
            "active_now": active_now,
            "active_24h": active_24h,
            "left":       left,
        }
    except Exception as e:
        logger.error(f"get_stats error: {e}")
        return {"total": 0, "active_now": 0, "active_24h": 0, "left": 0}

# ===================== CHANNEL HELPERS =====================
def add_channel(channel_id: int, name: str, username: str = "", invite_link: str = ""):
    try:
        channels_col.update_one(
            {"channel_id": channel_id},
            {
                "$set": {
                    "channel_id": channel_id,
                    "name": name,
                    "username": username.replace("@", ""),
                    "invite_link": invite_link,
                    "is_active": True,
                    "added_at": datetime.utcnow(),
                }
            },
            upsert=True,
        )
        return True
    except Exception as e:
        logger.error(f"add_channel error: {e}")
        return False

def remove_channel(channel_id: int):
    try:
        channels_col.delete_one({"channel_id": channel_id})
        return True
    except Exception as e:
        logger.error(f"remove_channel error: {e}")
        return False

def get_channels():
    try:
        return list(channels_col.find({"is_active": True}))
    except Exception as e:
        logger.error(f"get_channels error: {e}")
        return []

# ===================== PENDING JOIN REQUEST HELPERS =====================
def mark_join_request(user_id: int, channel_id: int):
    try:
        pending_col.update_one(
            {"user_id": user_id, "channel_id": channel_id},
            {"$set": {"status": "pending", "requested_at": datetime.utcnow()}},
            upsert=True,
        )
    except Exception as e:
        logger.error(f"mark_join_request error: {e}")

def has_pending_request(user_id: int, channel_id: int) -> bool:
    try:
        return pending_col.find_one({"user_id": user_id, "channel_id": channel_id, "status": "pending"}) is not None
    except Exception as e:
        logger.error(f"has_pending_request error: {e}")
        return False

def clear_join_request(user_id: int, channel_id: int):
    try:
        pending_col.update_one(
            {"user_id": user_id, "channel_id": channel_id},
            {"$set": {"status": "approved"}},
        )
    except Exception as e:
        logger.error(f"clear_join_request error: {e}")

# ===================== CHECK MEMBERSHIP =====================
async def get_not_joined(user_id: int, context) -> list:
    """Returns list of channels user hasn't joined yet."""
    all_channels = get_channels()
    not_joined = []

    for ch in all_channels:
        try:
            member = await context.bot.get_chat_member(ch["channel_id"], user_id)
            if member.status in ("member", "administrator", "creator"):
                clear_join_request(user_id, ch["channel_id"])
                continue
            elif has_pending_request(user_id, ch["channel_id"]):
                # Private channel — request sent, treat as OK
                continue
            else:
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)

    return not_joined

# ===================== KEYBOARDS =====================
def welcome_keyboard():
    mini_app = get_setting("mini_app_url", DEFAULT_MINI_APP_URL)
    rows = []
    if mini_app:
        rows.append([InlineKeyboardButton("🎮 Mini App খুলুন", web_app={"url": mini_app})])
    for ch in get_channels():
        url = ch.get("invite_link") or (f"https://t.me/{ch['username']}" if ch.get("username") else None)
        if url:
            rows.append([InlineKeyboardButton(f"📢 {ch['name']}", url=url)])
    return InlineKeyboardMarkup(rows) if rows else None

def admin_keyboard():
    rows = [
        [
            InlineKeyboardButton("📢 Channel যোগ করুন", callback_data="admin_add_channel"),
            InlineKeyboardButton("🗑 Channel মুছুন",    callback_data="admin_del_channel"),
        ],
        [
            InlineKeyboardButton("📊 User Statistics",  callback_data="admin_stats"),
            InlineKeyboardButton("📤 Broadcast",        callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("🔗 Mini App URL সেট", callback_data="admin_set_url"),
        ],
        [InlineKeyboardButton("❌ বন্ধ করুন", callback_data="admin_close")],
    ]
    return InlineKeyboardMarkup(rows)

def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_main")]])

# ===================== /start =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username or "", user.first_name)

    not_joined = await get_not_joined(user.id, context)

    if not_joined:
        # Force join screen
        text = (
            f"👋 হ্যালো **{user.first_name}**!\n\n"
            f"🔒 বট ব্যবহার করতে নিচের চ্যানেলগুলোতে **Join** করুন:\n\n"
        )
        for i, ch in enumerate(not_joined, 1):
            text += f"{i}. 📢 **{ch['name']}**\n"

        text += "\nJoin করার পর **✅ আমি Join করেছি** বাটনে ক্লিক করুন।"

        keyboard = []
        for ch in not_joined:
            url = ch.get("invite_link") or (f"https://t.me/{ch['username']}" if ch.get("username") else None)
            if url:
                keyboard.append([InlineKeyboardButton(f"➕ {ch['name']}", url=url)])
        keyboard.append([InlineKeyboardButton("✅ আমি Join করেছি", callback_data="check_join")])

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ✅ All joined — show welcome
    await send_welcome(update.message, user)

async def send_welcome(message, user):
    mini_app = get_setting("mini_app_url", DEFAULT_MINI_APP_URL)

    text = (
        f"🎉 **স্বাগতম {user.first_name}!**\n\n"
        f"আমাদের বটে আপনাকে স্বাগত জানাই! 🙏\n\n"
        f"নিচের বাটনে ক্লিক করে **Mini App** খুলুন এবং সব কন্টেন্ট উপভোগ করুন।\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ সম্পূর্ণ ফ্রি!\n"
        f"✅ প্রতিদিন নতুন আপডেট\n"
        f"✅ HD Quality\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 **Enjoy!**"
    )

    keyboard = []
    if mini_app:
        keyboard.append([InlineKeyboardButton("🎮 Mini App খুলুন", web_app={"url": mini_app})])

    for ch in get_channels():
        url = ch.get("invite_link") or (f"https://t.me/{ch['username']}" if ch.get("username") else None)
        if url:
            keyboard.append([InlineKeyboardButton(f"📢 {ch['name']}", url=url)])

    await message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
        parse_mode=ParseMode.MARKDOWN,
    )

# ===================== /admin =====================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    stats = get_stats()
    text = (
        f"🔧 **Admin Panel**\n\n"
        f"👥 Total Users: **{stats['total']}**\n"
        f"✅ Active Users: **{stats['active_now']}**\n"
        f"🔥 Active (24h): **{stats['active_24h']}**\n"
        f"❌ Left/Blocked: **{stats['left']}**\n"
    )
    await update.message.reply_text(text, reply_markup=admin_keyboard(), parse_mode=ParseMode.MARKDOWN)

# ===================== CALLBACK HANDLER =====================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    # ── Check join ──
    if data == "check_join":
        not_joined = await get_not_joined(user_id, context)
        if not_joined:
            names = ", ".join(f"**{ch['name']}**" for ch in not_joined)
            await query.answer(f"❌ এখনো join করননি: {names}", show_alert=True)
        else:
            await query.message.delete()
            await send_welcome(query.message, query.from_user)
        return

    # ── Admin only from here ──
    if user_id != ADMIN_ID:
        return

    if data == "admin_main":
        stats = get_stats()
        text = (
            f"🔧 **Admin Panel**\n\n"
            f"👥 Total: **{stats['total']}**\n"
            f"✅ Active: **{stats['active_now']}**\n"
            f"🔥 Active (24h): **{stats['active_24h']}**\n"
            f"❌ Left: **{stats['left']}**\n"
        )
        await query.edit_message_text(text, reply_markup=admin_keyboard(), parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_close":
        await query.message.delete()

    elif data == "admin_stats":
        stats = get_stats()
        text = (
            f"📊 **User Statistics**\n\n"
            f"👥 মোট Start করেছে: **{stats['total']}**\n"
            f"✅ এখন Active আছে: **{stats['active_now']}**\n"
            f"🔥 গত ২৪ ঘণ্টায় Active: **{stats['active_24h']}**\n"
            f"❌ চলে গেছে / Block করেছে: **{stats['left']}**\n"
        )
        await query.edit_message_text(text, reply_markup=back_keyboard(), parse_mode=ParseMode.MARKDOWN)

    elif data == "admin_broadcast":
        admin_broadcast_mode.add(user_id)
        await query.edit_message_text(
            "📢 **Broadcast Mode চালু!**\n\nএখন যে message পাঠাবেন (text/photo/video) সেটা সব user এর কাছে যাবে।\n\n/cancel লিখলে বাতিল হবে।",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "admin_add_channel":
        context.user_data["admin_action"] = "add_channel"
        await query.edit_message_text(
            "➕ **Channel যোগ করুন**\n\nনিচের format এ পাঠান:\n\n`channel_id|নাম|username_or_invite_link`\n\n**উদাহরণ (public):**\n`-1001234567890|আমার চ্যানেল|mychannel`\n\n**উদাহরণ (private):**\n`-1001234567890|প্রাইভেট চ্যানেল|https://t.me/+xxxxx`\n\n/cancel করতে পারেন।",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "admin_del_channel":
        channels = get_channels()
        if not channels:
            await query.edit_message_text("❌ কোনো channel নেই।", reply_markup=back_keyboard())
            return
        rows = []
        for ch in channels:
            rows.append([InlineKeyboardButton(f"🗑 {ch['name']}", callback_data=f"delch_{ch['channel_id']}")])
        rows.append([InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_main")])
        await query.edit_message_text("কোন channel মুছবেন?", reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("delch_"):
        ch_id = int(data.split("_", 1)[1])
        remove_channel(ch_id)
        await query.edit_message_text("✅ Channel মুছে ফেলা হয়েছে।", reply_markup=back_keyboard())

    elif data == "admin_set_url":
        context.user_data["admin_action"] = "set_url"
        await query.edit_message_text(
            "🔗 **Mini App URL সেট করুন**\n\nনতুন URL পাঠান:\n\n`https://your-app.vercel.app/`\n\n/cancel করতে পারেন।",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )

# ===================== MESSAGE HANDLER =====================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message

    # ── /cancel ──
    if message.text and message.text.strip() == "/cancel":
        admin_broadcast_mode.discard(user_id)
        context.user_data.pop("admin_action", None)
        await message.reply_text("❌ বাতিল।")
        return

    # ── Admin actions ──
    if user_id == ADMIN_ID:

        # Broadcast mode
        if user_id in admin_broadcast_mode:
            admin_broadcast_mode.discard(user_id)
            all_users = get_all_user_ids()

            if not all_users:
                await message.reply_text("❌ কোনো user নেই।")
                return

            progress_msg = await message.reply_text(f"📤 Broadcast শুরু হচ্ছে... ({len(all_users)} জন)")

            success = 0
            failed  = 0

            for uid in all_users:
                try:
                    if message.photo:
                        await context.bot.send_photo(
                            uid,
                            message.photo[-1].file_id,
                            caption=message.caption,
                            parse_mode=ParseMode.MARKDOWN if message.caption else None,
                        )
                    elif message.video:
                        await context.bot.send_video(
                            uid,
                            message.video.file_id,
                            caption=message.caption,
                            parse_mode=ParseMode.MARKDOWN if message.caption else None,
                        )
                    elif message.animation:
                        await context.bot.send_animation(
                            uid,
                            message.animation.file_id,
                            caption=message.caption,
                            parse_mode=ParseMode.MARKDOWN if message.caption else None,
                        )
                    elif message.document:
                        await context.bot.send_document(
                            uid,
                            message.document.file_id,
                            caption=message.caption,
                            parse_mode=ParseMode.MARKDOWN if message.caption else None,
                        )
                    elif message.text:
                        await context.bot.send_message(
                            uid,
                            message.text,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    success += 1
                except TelegramError as e:
                    failed += 1
                    err = str(e).lower()
                    if "blocked" in err or "deactivated" in err or "not found" in err:
                        mark_user_left(uid)

            await progress_msg.edit_text(
                f"✅ **Broadcast সম্পন্ন!**\n\n"
                f"✅ সফল: {success}\n"
                f"❌ ব্যর্থ: {failed}\n"
                f"📊 মোট: {len(all_users)}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Admin action: add_channel
        action = context.user_data.get("admin_action")

        if action == "add_channel":
            context.user_data.pop("admin_action", None)
            parts = message.text.strip().split("|")
            if len(parts) < 3:
                await message.reply_text(
                    "❌ Format ঠিক নেই।\n\nFormat: `channel_id|নাম|username_or_invite_link`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return
            try:
                ch_id    = int(parts[0].strip())
                ch_name  = parts[1].strip()
                ch_third = parts[2].strip()

                # Detect if it's an invite link or username
                if ch_third.startswith("http"):
                    add_channel(ch_id, ch_name, invite_link=ch_third)
                else:
                    add_channel(ch_id, ch_name, username=ch_third)

                await message.reply_text(f"✅ চ্যানেল যোগ হয়েছে: **{ch_name}**", parse_mode=ParseMode.MARKDOWN)
            except ValueError:
                await message.reply_text("❌ Channel ID অবশ্যই নম্বর হতে হবে।")
            return

        if action == "set_url":
            context.user_data.pop("admin_action", None)
            new_url = message.text.strip()
            set_setting("mini_app_url", new_url)
            await message.reply_text(f"✅ Mini App URL আপডেট হয়েছে:\n`{new_url}`", parse_mode=ParseMode.MARKDOWN)
            return

    # ── Normal user: auto reply ──
    mini_app = get_setting("mini_app_url", DEFAULT_MINI_APP_URL)
    kb = [[InlineKeyboardButton("🎮 Mini App", web_app={"url": mini_app})]] if mini_app else None
    await message.reply_text(
        "👋 Mini App খুলতে /start লিখুন!",
        reply_markup=InlineKeyboardMarkup(kb) if kb else None,
    )

# ===================== CHAT JOIN REQUEST =====================
async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req  = update.chat_join_request
    user = req.from_user
    chat = req.chat
    mark_join_request(user.id, chat.id)
    logger.info(f"✅ Join request: user {user.id} → channel {chat.id}")

# ===================== ERROR HANDLER =====================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import traceback
    logger.error(f"Update {update} caused error: {context.error}")
    logger.error(traceback.format_exc())

# ===================== MAIN =====================
def main():
    logger.info("🚀 Bot starting...")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    logger.info(f"✅ Bot running | Admin: {ADMIN_ID}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
