import asyncio
import logging
import os
import random
import re
import sqlite3
import sys
from datetime import datetime

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import (
    ChatWriteForbidden,
    FloodWait,
    PeerIdInvalid,
    UserBannedInChannel,
    UsernameInvalid,
    UsernameNotOccupied,
)

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_NAME = os.getenv("SESSION_NAME", "userbot_session")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "")

DELAY_MIN = int(os.getenv("DELAY_MIN", "300"))
DELAY_MAX = int(os.getenv("DELAY_MAX", "600"))
JOIN_DELAY_MIN = int(os.getenv("JOIN_DELAY_MIN", "15"))
JOIN_DELAY_MAX = int(os.getenv("JOIN_DELAY_MAX", "45"))
DB_PATH = os.getenv("DB_PATH", "sender.db")

try:
    ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip()}
except ValueError:
    ADMIN_IDS = set()


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[37m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = super().format(record)
        return f"{color}[{ts}] {msg}{self.RESET}"


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColorFormatter("%(levelname)-8s %(message)s"))
logger = logging.getLogger("sender")
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logging.getLogger("pyrogram").setLevel(logging.WARNING)


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT UNIQUE NOT NULL,
            active INTEGER DEFAULT 1
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS send_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            status TEXT NOT NULL,
            note TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def add_chats(conn: sqlite3.Connection, targets: list[str]) -> int:
    added = 0
    cur = conn.cursor()
    for raw in targets:
        t = resolve_target(raw)
        if not t:
            continue
        cur.execute("INSERT OR IGNORE INTO chats (target, active) VALUES (?, 1)", (t,))
        if cur.rowcount:
            added += 1
    conn.commit()
    return added


def get_active_chats(conn: sqlite3.Connection) -> list[str]:
    cur = conn.cursor()
    cur.execute("SELECT target FROM chats WHERE active = 1")
    return [row[0] for row in cur.fetchall()]


def deactivate_chat(conn: sqlite3.Connection, target: str):
    cur = conn.cursor()
    cur.execute("UPDATE chats SET active = 0 WHERE target = ?", (target,))
    conn.commit()


def log_send(conn: sqlite3.Connection, target: str, status: str, note: str = ""):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO send_history (target, sent_at, status, note) VALUES (?, ?, ?, ?)",
        (target, datetime.now().isoformat(timespec="seconds"), status, note[:200]),
    )
    conn.commit()


def get_history(conn: sqlite3.Connection, limit: int = 20):
    cur = conn.cursor()
    cur.execute(
        "SELECT sent_at, target, status, note FROM send_history ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return cur.fetchall()


def spin(template: str) -> str:
    def replace_one(match: re.Match) -> str:
        return random.choice(match.group(1).split("|"))

    result = template
    while "{" in result:
        prev = result
        result = re.sub(r"\{([^{}]+)\}", replace_one, result)
        if result == prev:
            break
    return result


def resolve_target(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if "joinchat" in raw or raw.startswith("t.me/+") or raw.startswith("https://t.me/+"):
        return raw
    raw = re.sub(r"^(https?://)?(www\.)?t\.me/", "", raw)
    raw = raw.lstrip("@")
    return raw


class BlastService:
    def __init__(self, user_app: Client, conn: sqlite3.Connection):
        self.user_app = user_app
        self.conn = conn
        self.running = False
        self.task: asyncio.Task | None = None

    async def ensure_joined(self, target: str) -> bool:
        try:
            chat = await self.user_app.get_chat(target)
            if chat.type.name in ("SUPERGROUP", "CHANNEL", "GROUP"):
                try:
                    member = await self.user_app.get_chat_member(chat.id, "me")
                    if member.status.name in ("BANNED", "LEFT"):
                        raise ValueError("left")
                except Exception:
                    await self.user_app.join_chat(target)
                    await asyncio.sleep(random.randint(JOIN_DELAY_MIN, JOIN_DELAY_MAX))
            return True
        except FloodWait as e:
            await asyncio.sleep(e.value + 5)
            return False
        except Exception as e:
            logger.error(f"Join error {target}: {e}")
            return False

    async def send_to_chat(self, raw_target: str, template: str) -> bool:
        target = resolve_target(raw_target)
        text = spin(template)
        try:
            if not await self.ensure_joined(target):
                log_send(self.conn, raw_target, "error", "join failed")
                return False
            await self.user_app.send_message(target, text)
            log_send(self.conn, raw_target, "ok", text[:80].replace("\n", " "))
            return True
        except FloodWait as e:
            await asyncio.sleep(e.value + random.randint(5, 15))
            return False
        except (ChatWriteForbidden, UserBannedInChannel) as e:
            log_send(self.conn, raw_target, "error", str(e))
            deactivate_chat(self.conn, raw_target)
            return False
        except (UsernameInvalid, UsernameNotOccupied, PeerIdInvalid) as e:
            log_send(self.conn, raw_target, "error", str(e))
            deactivate_chat(self.conn, raw_target)
            return False
        except Exception as e:
            log_send(self.conn, raw_target, "error", str(e))
            return False

    async def run(self) -> tuple[int, int]:
        chats = get_active_chats(self.conn)
        template = get_setting(self.conn, "template", "")
        if not chats or not template:
            return 0, 0

        random.shuffle(chats)
        ok = 0
        fail = 0
        for i, target in enumerate(chats, 1):
            sent = await self.send_to_chat(target, template)
            ok += 1 if sent else 0
            fail += 0 if sent else 1
            if i < len(chats):
                await asyncio.sleep(random.randint(DELAY_MIN, DELAY_MAX))
        return ok, fail


async def main():
    if API_ID == 0 or not API_HASH:
        logger.critical("Заполните API_ID/API_HASH в .env")
        return
    if not BOT_TOKEN:
        logger.critical("Заполните BOT_TOKEN в .env")
        return

    conn = init_db()
    user_app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)
    bot_app = Client("control_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
    blast = BlastService(user_app, conn)
    user_states: dict[int, str] = {}

    def is_admin(user_id: int | None) -> bool:
        return bool(user_id) and (not ADMIN_IDS or user_id in ADMIN_IDS)

    def main_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Статус", callback_data="menu_status")],
                [InlineKeyboardButton("Добавить чаты", callback_data="menu_add")],
                [InlineKeyboardButton("Установить шаблон", callback_data="menu_template")],
                [InlineKeyboardButton("Показать шаблон", callback_data="menu_show_template")],
                [InlineKeyboardButton("Запустить рассылку", callback_data="menu_start_blast")],
                [InlineKeyboardButton("История", callback_data="menu_history")],
            ]
        )

    async def send_menu(chat_id: int):
        await bot_app.send_message(chat_id, "Панель управления рассылкой:", reply_markup=main_keyboard())

    @bot_app.on_message(filters.command(["start", "help"]))
    async def cmd_start(_, message):
        user_id = message.from_user.id if message.from_user else None
        if not is_admin(user_id):
            return
        await send_menu(message.chat.id)

    @bot_app.on_callback_query()
    async def on_callback(_, callback_query):
        user_id = callback_query.from_user.id if callback_query.from_user else None
        if not is_admin(user_id):
            await callback_query.answer("Нет доступа", show_alert=True)
            return

        data = callback_query.data or ""
        await callback_query.answer()

        if data == "menu_status":
            chats = len(get_active_chats(conn))
            has_template = bool(get_setting(conn, "template", ""))
            await callback_query.message.reply(
                f"Статус:\nАктивных чатов: {chats}\nШаблон: {'да' if has_template else 'нет'}\nРассылка: {'идет' if blast.running else 'остановлена'}"
            )
        elif data == "menu_add":
            user_states[user_id] = "await_chats"
            await callback_query.message.reply(
                "Отправьте список чатов одним сообщением, каждый с новой строки."
            )
        elif data == "menu_template":
            user_states[user_id] = "await_template"
            await callback_query.message.reply("Отправьте новый шаблон одним сообщением.")
        elif data == "menu_show_template":
            template = get_setting(conn, "template", "")
            if not template:
                await callback_query.message.reply("Шаблон пока не задан.")
            else:
                await callback_query.message.reply(f"Текущий шаблон:\n\n{template}")
        elif data == "menu_history":
            rows = get_history(conn, 20)
            if not rows:
                await callback_query.message.reply("История пустая.")
            else:
                lines = []
                for sent_at, target, status, note in rows:
                    icon = "OK" if status == "ok" else "ERR"
                    lines.append(f"{sent_at} | {target} | {icon} | {note or '-'}")
                text = "\n".join(lines)
                for chunk_start in range(0, len(text), 3900):
                    await callback_query.message.reply(text[chunk_start:chunk_start + 3900])
        elif data == "menu_start_blast":
            if blast.running:
                await callback_query.message.reply("Рассылка уже выполняется.")
                return
            chats = get_active_chats(conn)
            template = get_setting(conn, "template", "")
            if not chats:
                await callback_query.message.reply("Список чатов пуст. Сначала добавьте чаты.")
                return
            if not template:
                await callback_query.message.reply("Шаблон не задан. Сначала сохраните шаблон.")
                return

            blast.running = True
            await callback_query.message.reply(f"Запускаю рассылку по {len(chats)} чатам...")

            async def worker(chat_id: int):
                try:
                    ok, fail = await blast.run()
                    await bot_app.send_message(chat_id, f"Рассылка завершена. Успешно: {ok}, ошибок: {fail}")
                finally:
                    blast.running = False

            blast.task = asyncio.create_task(worker(callback_query.message.chat.id))

    @bot_app.on_message(filters.text & ~filters.command(["start", "help"]))
    async def on_text(_, message):
        user_id = message.from_user.id if message.from_user else None
        if not is_admin(user_id):
            return

        state = user_states.get(user_id, "")
        if state == "await_chats":
            targets = [line.strip() for line in message.text.splitlines() if line.strip()]
            added = add_chats(conn, targets)
            user_states.pop(user_id, None)
            await message.reply(f"Добавлено новых чатов: {added}", reply_markup=main_keyboard())
        elif state == "await_template":
            template = message.text.strip()
            if not template:
                await message.reply("Пустой шаблон не сохранен. Отправьте текст шаблона.")
                return
            set_setting(conn, "template", template)
            user_states.pop(user_id, None)
            await message.reply(f"Шаблон сохранен. Пример:\n\n{spin(template)}", reply_markup=main_keyboard())
        else:
            await message.reply("Нажмите /start и используйте кнопки меню.", reply_markup=main_keyboard())

    await user_app.start()
    me = await user_app.get_me()
    logger.info(f"Userbot login: {me.id} @{me.username}")
    await bot_app.start()
    bot_me = await bot_app.get_me()
    logger.info(f"Control bot started: @{bot_me.username}")

    try:
        await asyncio.Event().wait()
    finally:
        await bot_app.stop()
        await user_app.stop()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
