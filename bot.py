import threading
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from core import create_key, KEY_DAYS, BOT_TOKEN, BOT_ADMIN

_app = None
_thread = None


def set_creds(token: str, admin: str) -> bool:
    token, admin = token.strip(), admin.strip()
    if not token or ":" not in token:
        return False
    if not admin.isdigit():
        return False
    BOT_TOKEN.write_text(token, encoding="utf-8")
    BOT_ADMIN.write_text(admin, encoding="utf-8")
    return True


def _read():
    if not BOT_TOKEN.exists() or not BOT_ADMIN.exists():
        return None, None
    t = BOT_TOKEN.read_text(encoding="utf-8").strip()
    a = BOT_ADMIN.read_text(encoding="utf-8").strip()
    if not t or not a.isdigit():
        return None, None
    return t, int(a)


async def _start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _, admin = _read()
    if admin is None:
        await update.message.reply_text("бот не настроен")
        return
    if update.effective_user.id != admin:
        await update.message.reply_text("нет доступа")
        return
    kb = [
        [InlineKeyboardButton("trial", callback_data="trial"),
         InlineKeyboardButton("month", callback_data="month")],
        [InlineKeyboardButton("year", callback_data="year"),
         InlineKeyboardButton("for_good", callback_data="for_good")],
        [InlineKeyboardButton("своё число", callback_data="custom")],
    ]
    await update.message.reply_text("выбери тип ключа:", reply_markup=InlineKeyboardMarkup(kb))


async def _on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "custom":
        await q.edit_message_text("отправь: /custom 7")
        return
    try:
        key = create_key(q.data, KEY_DAYS.get(q.data, 0))
    except Exception as e:
        await q.edit_message_text(f"ошибка: {e}")
        return
    await q.edit_message_text(f"ключ ({q.data}):\n`{key}`", parse_mode="Markdown")


async def _custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _, admin = _read()
    if admin is None or update.effective_user.id != admin:
        return
    try:
        days = int(ctx.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("формат: /custom 7")
        return
    try:
        key = create_key("custom", days)
    except Exception as e:
        await update.message.reply_text(f"ошибка: {e}")
        return
    await update.message.reply_text(f"ключ (custom {days} дн.):\n`{key}`", parse_mode="Markdown")


def _run(token: str):
    global _app
    _app = Application.builder().token(token).build()
    _app.add_handler(CommandHandler("start", _start))
    _app.add_handler(CommandHandler("custom", _custom))
    _app.add_handler(CallbackQueryHandler(_on_button))
    _app.run_polling()


def start_bot() -> tuple[bool, str]:
    global _thread
    token, _ = _read()
    if not token:
        return False, "токен бота не задан"
    if _thread and _thread.is_alive():
        return True, "бот уже запущен"
    _thread = threading.Thread(target=_run, args=(token,), daemon=True)
    _thread.start()
    return True, "бот запущен"
