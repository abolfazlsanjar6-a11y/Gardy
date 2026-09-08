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
                "🛡️ گاردی
