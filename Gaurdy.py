import os
import re
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone

from telegram import Update, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("GUARDY_TOKEN")

if not TOKEN:
    raise RuntimeError("GUARDY_TOKEN تنظیم نشده است.")

DB = "guardy.db"

conn = sqlite3.connect(DB, check_same_thread=False)
conn.row_factory = sqlite3.Row


def db(sql, params=(), fetch=False):
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    if fetch:
        return cur.fetchall()
    return []


def init_db():
    db("""
    CREATE TABLE IF NOT EXISTS roles (
        chat_id INTEGER,
        user_id INTEGER,
        role TEXT,
        PRIMARY KEY(chat_id,user_id)
    )
    """)

    db("""
    CREATE TABLE IF NOT EXISTS groups (
        chat_id INTEGER PRIMARY KEY,
        owner_id INTEGER,
        anti_link INTEGER DEFAULT 0,
        force_membership INTEGER DEFAULT 0,
        required_channel TEXT DEFAULT '',
        welcome INTEGER DEFAULT 1,
        anti_spam INTEGER DEFAULT 1
    )
    """)

    db("""
    CREATE TABLE IF NOT EXISTS nicknames (
        chat_id INTEGER,
        user_id INTEGER,
        nickname TEXT,
        PRIMARY KEY(chat_id,user_id)
    )
    """)

    db("""
    CREATE TABLE IF NOT EXISTS warnings (
        chat_id INTEGER,
        user_id INTEGER,
        count INTEGER DEFAULT 0,
        PRIMARY KEY(chat_id,user_id)
    )
    """)

    db("""
    CREATE TABLE IF NOT EXISTS mutes (
        chat_id INTEGER,
        user_id INTEGER,
        until INTEGER,
        PRIMARY KEY(chat_id,user_id)
    )
    """)

    db("""
    CREATE TABLE IF NOT EXISTS word_filters (
        chat_id INTEGER,
        word TEXT,
        PRIMARY KEY(chat_id,word)
    )
    """)

    db("""
    CREATE TABLE IF NOT EXISTS replies (
        chat_id INTEGER,
        trigger TEXT,
        response TEXT,
        PRIMARY KEY(chat_id,trigger)
    )
    """)

    db("""
    CREATE TABLE IF NOT EXISTS owner_chats (
        user_id INTEGER PRIMARY KEY,
        chat_id INTEGER
    )
    """)

    db("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        actor_id INTEGER,
        action TEXT,
        target_id INTEGER,
        created_at TEXT
    )
    """)


def role(chat_id, user_id):
    rows = db(
        "SELECT role FROM roles WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
        True
    )
    if rows:
        return rows[0]["role"]

    rows = db(
        "SELECT role FROM roles WHERE chat_id=0 AND user_id=?",
        (user_id,),
        True
    )
    return rows[0]["role"] if rows else "user"


def set_role(chat_id, user_id, value):
    db(
        "INSERT OR REPLACE INTO roles(chat_id,user_id,role) VALUES(?,?,?)",
        (chat_id, user_id, value)
    )


def is_guardy_staff(chat_id, user_id):
    return role(chat_id, user_id) in ("owner", "admin", "special")


def log_action(chat_id, actor, action, target=None):
    db(
        """INSERT INTO logs
        (chat_id,actor_id,action,target_id,created_at)
        VALUES(?,?,?,?,?)""",
        (
            chat_id,
            actor,
            action,
            target,
            datetime.now(timezone.utc).isoformat()
        )
    )


def group_exists(chat_id):
    return bool(db(
        "SELECT chat_id FROM groups WHERE chat_id=?",
        (chat_id,),
        True
    ))


async def ensure_group(update: Update):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return

    if not group_exists(chat.id):
        admins = await update.get_bot().get_chat_administrators(chat.id)

        owner_id = None
        for a in admins:
            if a.status == "creator":
                owner_id = a.user.id
                break

        db(
            "INSERT OR IGNORE INTO groups(chat_id,owner_id) VALUES(?,?)",
            (chat.id, owner_id)
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if update.effective_chat.type == "private":
        db(
            "INSERT OR REPLACE INTO owner_chats(user_id,chat_id) VALUES(?,?)",
            (user.id, update.effective_chat.id)
        )

        if not db(
            "SELECT role FROM roles WHERE chat_id=0 AND user_id=?",
            (user.id,),
            True
        ):
            set_role(0, user.id, "owner")
            await update.message.reply_text(
                "👑 شما به عنوان مالک گاردی ثبت شدید."
            )
        else:
            await update.message.reply_text(
                "🛡️ گاردی فعاله."
            )
        return

    await ensure_group(update)
    await update.message.reply_text(
        "🛡️ سلام! من گاردی هستم.\n"
        "برای دیدن راهنما بنویس: راهنما"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_group(update)

    text = """
🛡️ راهنمای گاردی

👑 مدیریت گاردی:
تنظیم ادمین
حذف ادمین
تنظیم ویژه
حذف ویژه

🔨 مدیریت اعضا:
سیک طرف
سکوت 1
سکوت 10
سکوت
حذف سکوت
اخطار

🏷️ لقب:
تنظیم لقب سلطان
تنظیم لقب
حذف لقب

👤 اطلاعات:
وضعیت

⚙️ تنظیمات:
ضدلینک روشن
ضدلینک خاموش
عضویت اجباری روشن
عضویت اجباری خاموش

💬 پاسخ خودکار:
پاسخ سلام = سلام 👋 خوش اومدی
"""

    await update.message.reply_text(text)


async def replied_target(update):
    msg = update.message
    if not msg or not msg.reply_to_message:
        return None

    return msg.reply_to_message.from_user


async def permission_denied(update):
    await update.message.reply_text(
        "⛔ شما دسترسی گاردی ندارید."
    )


async def protect_check(update, target):
    r = role(update.effective_chat.id, target.id)

    if r == "owner":
        await update.message.reply_text(
            "👑 این کاربر مالک گاردی است، نمی‌تونم این کارو انجام بدم."
        )
        return False

    if r == "admin":
        await update.message.reply_text(
            "🛡️ این کاربر ادمین گاردی است، نمی‌تونم این کارو انجام بدم."
        )
        return False

    if r == "special":
        await update.message.reply_text(
            "⭐ این کاربر ویژه گاردی است، نمی‌تونم این کارو انجام بدم."
        )
        return False

    return True


async def kick_user(update, context, target):
    if not await protect_check(update, target):
        return

    chat_id = update.effective_chat.id

    try:
        await context.bot.ban_chat_member(chat_id, target.id)
        await context.bot.unban_chat_member(chat_id, target.id)

        log_action(chat_id, update.effective_user.id, "kick", target.id)

        await update.message.reply_text("سیک شد 😂")

    except Exception as e:
        await update.message.reply_text(
            f"❌ نتونستم سیک کنم.\n{e}"
        )


async def mute_user(update, context, target, minutes=None):
    if not await protect_check(update, target):
        return

    chat_id = update.effective_chat.id

    try:
        if minutes is None:
            until = 0
            text = "🔇 کاربر برای همیشه سکوت شد."
        else:
            until = int(
                (datetime.now(timezone.utc) +
                 timedelta(minutes=minutes)).timestamp()
            )
            text = f"🔇 کاربر: {target.first_name}\n⏱️ به مدت {minutes} دقیقه سکوت شد."

        permissions = ChatPermissions(
            can_send_messages=False
        )

        await context.bot.restrict_chat_member(
            chat_id,
            target.id,
            permissions,
            until_date=None if until == 0 else datetime.fromtimestamp(
                until, timezone.utc
            )
        )

        db(
            "INSERT OR REPLACE INTO mutes(chat_id,user_id,until) VALUES(?,?,?)",
            (chat_id, target.id, until)
        )

        log_action(chat_id, update.effective_user.id, "mute", target.id)

        await update.message.reply_text(text)

    except Exception as e:
        await update.message.reply_text(
            f"❌ خطا در سکوت کردن:\n{e}"
        )


async def unmute_user(update, context, target):
    if not await protect_check(update, target):
        return

    chat_id = update.effective_chat.id

    try:
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )

        await context.bot.restrict_chat_member(
            chat_id,
            target.id,
            permissions
        )

        db(
            "DELETE FROM mutes WHERE chat_id=? AND user_id=?",
            (chat_id, target.id)
        )

        log_action(chat_id, update.effective_user.id, "unmute", target.id)

        await update.message.reply_text(
            f"🔊 سکوت کاربر {target.first_name} برداشته شد."
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ خطا:\n{e}"
        )


async def nickname(update, target, value):
    chat_id = update.effective_chat.id

    if not value:
        await update.message.reply_text(
            "🏷️ لقب موردنظر رو بعد از «تنظیم لقب» بنویس."
        )
        return

    db(
        "INSERT OR REPLACE INTO nicknames(chat_id,user_id,nickname) VALUES(?,?,?)",
        (chat_id, target.id, value)
    )

    await update.message.reply_text(
        f"✅ لقب تنظیم شد 🏷️ لقب: {value}"
    )


async def status(update, target):
    chat_id = update.effective_chat.id
    r = role(chat_id, target.id)

    names = {
        "owner": "👑 مالک",
        "admin": "🛡️ ادمین",
        "special": "⭐ ویژه",
        "user": "👤 کاربر"
    }

    rows = db(
        "SELECT nickname FROM nicknames WHERE chat_id=? AND user_id=?",
        (chat_id, target.id),
        True
    )

    nick = rows[0]["nickname"] if rows else "ندارد"

    await update.message.reply_text(
        f"👤 کاربر: {target.first_name}\n"
        f"🛡️ وضعیت گاردی: {names.get(r, '👤 کاربر')}\n"
        f"🏷️ لقب: {nick}"
    )


async def warning(update, context, target):
    chat_id = update.effective_chat.id

    rows = db(
        "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
        (chat_id, target.id),
        True
    )

    count = rows[0]["count"] + 1 if rows else 1

    db(
        "INSERT OR REPLACE INTO warnings(chat_id,user_id,count) VALUES(?,?,?)",
        (chat_id, target.id, count)
    )

    log_action(chat_id, update.effective_user.id, "warn", target.id)

    await update.message.reply_text(
        f"⚠️ {target.first_name} اخطار گرفت.\n"
        f"تعداد اخطار: {count}"
    )


async def role_command(update, context, target, new_role):
    if role(update.effective_chat.id, update.effective_user.id) != "owner":
        await permission_denied(update)
        return

    set_role(update.effective_chat.id, target.id, new_role)

    names = {
        "admin": "ادمین",
        "special": "ویژه",
        "user": "کاربر"
    }

    await update.message.reply_text(
        f"✅ {names.get(new_role, new_role)} تنظیم شد."
    )


async def process_text(update, context):
    msg = update.message
    if not msg or not msg.text:
        return

    if msg.chat.type not in ("group", "supergroup"):
        return

    await ensure_group(update)

    text = msg.text.strip()
    lower = text.lower()
    actor = msg.from_user
    chat_id = msg.chat.id

    # پاسخ‌های ساده
    if lower == "سلام":
        await msg.reply_text("سلام 👋 خوش اومدی")
        return

    if lower == "گاردی":
        await msg.reply_text("🛡️ جانم؟ گاردی اینجاست 😎")
        return

    # پاسخ‌های سفارشی
    rows = db(
        "SELECT response FROM replies WHERE chat_id=? AND trigger=?",
        (chat_id, lower),
        True
    )

    if rows:
        await msg.reply_text(rows[0]["response"])
        return

    # فقط کارکنان گاردی
    if not is_guardy_staff(chat_id, actor.id):
        return

    target = await replied_target(update)

    # تنظیم ادمین
    if lower == "تنظیم ادمین" and target:
        await role_command(update, context, target, "admin")
        return

    if lower == "حذف ادمین" and target:
        await role_command(update, context, target, "user")
        return

    if lower == "تنظیم ویژه" and target:
        if role(chat_id, actor.id) not in ("owner", "admin"):
            await permission_denied(update)
            return
        set_role(chat_id, target.id, "special")
        await msg.reply_text("⭐ کاربر ویژه شد.")
        return

    if lower == "حذف ویژه" and target:
        if role(chat_id, actor.id) not in ("owner", "admin"):
            await permission_denied(update)
            return
        set_role(chat_id, target.id, "user")
        await msg.reply_text("⭐ وضعیت ویژه حذف شد.")
        return

    # سیک
    if lower == "سیک طرف" and target:
        await kick_user(update, context, target)
        return

    # سکوت
    if lower == "سکوت" and target:
        await mute_user(update, context, target)
        return

    m = re.fullmatch(r"سکوت\s+(\d+)", lower)
    if m and target:
        minutes = int(m.group(1))
        if minutes <= 0:
            await msg.reply_text("❌ مدت باید بیشتر از صفر باشد.")
            return
        await mute_user(update, context, target, minutes)
        return

    if lower == "حذف سکوت" and target:
        await unmute_user(update, context, target)
        return

    # لقب
    if lower.startswith("تنظیم لقب") and target:
        value = text[len("تنظیم لقب"):].strip()
        await nickname(update, target, value)
        return

    if lower == "حذف لقب" and target:
        db(
            "DELETE FROM nicknames WHERE chat_id=? AND user_id=?",
            (chat_id, target.id)
        )
        await msg.reply_text("✅ لقب حذف شد.")
        return

    # وضعیت
    if lower == "وضعیت" and target:
        await status(update, target)
        return

    # اخطار
    if lower == "اخطار" and target:
        await warning(update, context, target)
        return

    # تنظیم پاسخ خودکار
    if lower.startswith("پاسخ ") and "=" in text:
        raw = text[6:]
        trigger, response = raw.split("=", 1)

        trigger = trigger.strip().lower()
        response = response.strip()

        if trigger and response:
            db(
                "INSERT OR REPLACE INTO replies(chat_id,trigger,response) VALUES(?,?,?)",
                (chat_id, trigger, response)
            )

            await msg.reply_text("✅ پاسخ خودکار تنظیم شد.")
        return

    # تنظیمات ضد لینک
    if lower == "ضدلینک روشن":
        db(
            "UPDATE groups SET anti_link=1 WHERE chat_id=?",
            (chat_id,)
        )
        await msg.reply_text("🔗 ضدلینک روشن شد.")
        return

    if lower == "ضدلینک خاموش":
        db(
            "UPDATE groups SET anti_link=0 WHERE chat_id=?",
            (chat_id,)
        )
        await msg.reply_text("🔗 ضدلینک خاموش شد.")
        return


async def handle_message(update, context):
    msg = update.message

    if not msg:
        return

    await ensure_group(update)

    # بررسی لینک
    if msg.chat.type in ("group", "supergroup"):
        rows = db(
            "SELECT anti_link FROM groups WHERE chat_id=?",
            (msg.chat.id,),
            True
        )

        anti_link = rows[0]["anti_link"] if rows else 0

        if anti_link and re.search(
            r"(https?://|www\.|t\.me/|telegram\.me/)",
            msg.text or "",
            re.I
        ):
            if not is_guardy_staff(msg.chat.id, msg.from_user.id):
                try:
                    await msg.delete()
                    await msg.chat.send_message(
                        "🚫 ارسال لینک مجاز نیست."
                    )
                except Exception:
                    pass
                return

    await process_text(update, context)


async def member_update(update, context):
    cm = update.chat_member

    if not cm:
        return

    chat = cm.chat

    if chat.type not in ("group", "supergroup"):
        return

    old = cm.old_chat_member.status
    new = cm.new_chat_member.status
    user = cm.new_chat_member.user

    if old in ("left", "kicked") and new in ("member", "restricted"):
        rows = db(
            "SELECT owner_id FROM groups WHERE chat_id=?",
            (chat.id,),
            True
        )

        if rows and rows[0]["owner_id"]:
            owner_id = rows[0]["owner_id"]

            owner_chat = db(
                "SELECT chat_id FROM owner_chats WHERE user_id=?",
                (owner_id,),
                True
            )

            if owner_chat:
                try:
                    await context.bot.send_message(
                        owner_chat[0]["chat_id"],
                        "👤 عضو جدید وارد شد\n\n"
                        f"نام: {user.first_name}\n"
                        f"آیدی: {user.id}\n"
                        f"گروه: {chat.title}\n"
                        f"زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                except Exception:
                    pass

        rows = db(
            "SELECT welcome FROM groups WHERE chat_id=?",
            (chat.id,),
            True
        )

        if rows and rows[0]["welcome"]:
            try:
                await context.bot.send_message(
                    chat.id,
                    f"👋 سلام {user.first_name}، خوش اومدی به {chat.title} ❤️"
                )
            except Exception:
                pass


def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(
        ChatMemberHandler(
            member_update,
            ChatMemberHandler.CHAT_MEMBER
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("Guardy is running...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
