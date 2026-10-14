
import asyncio
import os
import re
import html
import logging
import urllib.parse
import requests

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramBadRequest,
    TelegramRetryAfter,
)

# =========================================================
# НАЛАШТУВАННЯ
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME")
LEADS_WEBHOOK_URL = os.getenv("LEADS_WEBHOOK_URL")
MAKE_BOT_WEBHOOK_URL = os.getenv("MAKE_BOT_WEBHOOK_URL")

GROUP_CHAT_ID = -5233088810

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not MANAGER_USERNAME:
    raise ValueError("MANAGER_USERNAME is missing")

if not LEADS_WEBHOOK_URL:
    raise ValueError("LEADS_WEBHOOK_URL is missing")

MANAGER_USERNAME = MANAGER_USERNAME.lstrip("@")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# =========================================================
# ТИМЧАСОВІ ДАНІ
# =========================================================

user_leads = {}

broadcast_waiting_for_text = set()
pending_broadcasts = {}

broadcast_running = False

# =========================================================
# GOOGLE SHEETS / MAKE
# =========================================================

def post_lead(payload):
    try:
        response = requests.post(
            LEADS_WEBHOOK_URL,
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("Lead webhook error")


def post_bot_event(payload):
    if not MAKE_BOT_WEBHOOK_URL:
        return

    try:
        response = requests.post(
            MAKE_BOT_WEBHOOK_URL,
            json=payload,
            timeout=8,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("Bot event error")


def get_users_sync():
    try:
        response = requests.post(
            LEADS_WEBHOOK_URL,
            json={"action": "get_users"},
            timeout=30,
        )

        response.raise_for_status()
        data = response.json()

        if not isinstance(data, dict):
            raise ValueError("Invalid webhook response")

        if data.get("status") != "ok":
            raise ValueError(
                f"Webhook status: {data.get('status')}"
            )

        users = data.get("users", [])

        if not isinstance(users, list):
            raise ValueError("Users must be a list")

        return users

    except Exception:
        logger.exception("Get broadcast users error")
        raise


def update_status_sync(telegram_id, status):
    try:
        response = requests.post(
            LEADS_WEBHOOK_URL,
            json={
                "action": "update_status",
                "telegram_id": str(telegram_id),
                "bot_status": status,
            },
            timeout=15,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("Update user status error")


async def get_broadcast_users():
    return await asyncio.to_thread(get_users_sync)


async def update_user_status(telegram_id, status):
    await asyncio.to_thread(
        update_status_sync,
        telegram_id,
        status,
    )


async def send_lead(user, source):
    payload = {
        "telegram_id": user.id,
        "username": (
            f"@{user.username}"
            if user.username else ""
        ),
        "full_name": user.full_name,
        "source": source,
    }

    await asyncio.to_thread(post_lead, payload)


# =========================================================
# КНОПКИ
# =========================================================

def manager_link(text):
    return (
        f"https://t.me/{MANAGER_USERNAME}"
        f"?text={urllib.parse.quote(text)}"
    )


def main_buttons():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Дізнатися вартість моєї роботи 📚",
                    url=manager_link(
                        "Я хочу дізнатись вартість моєї роботи!"
                    ),
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="Я з Instagram 🙋🏽‍♀️",
                    url=manager_link("Я з Instagram"),
                )
            ],
        ]
    )


async def show_chat_cta(message):
    await message.answer(
        "Щоб зв'язатись з нашим менеджером, "
        "натискай кнопку нижче 👇🏼",
        reply_markup=main_buttons(),
    )


# =========================================================
# ВИЗНАЧЕННЯ ТИПУ ПОВІДОМЛЕННЯ
# =========================================================

def extract_message_content(message):
    if message.text:
        return message.text

    if message.document:
        name = message.document.file_name or "без назви"
        return f"[Документ: {name}]\n{message.caption or ''}"

    if message.photo:
        return f"[Фото]\n{message.caption or ''}"

    if message.video:
        return f"[Відео]\n{message.caption or ''}"

    if message.voice:
        return "[Голосове повідомлення]"

    if message.audio:
        name = (
            message.audio.title
            or message.audio.file_name
            or "без назви"
        )
        return f"[Аудіо: {name}]\n{message.caption or ''}"

    if message.sticker:
        return f"[Стікер {message.sticker.emoji or ''}]"

    if message.video_note:
        return "[Відеоповідомлення]"

    if message.animation:
        return f"[GIF]\n{message.caption or ''}"

    return "[Невідомий тип повідомлення]"


# =========================================================
# /START
# =========================================================

@dp.message(CommandStart())
async def start(message: types.Message):
    await show_chat_cta(message)

    parts = (message.text or "").split(maxsplit=1)
    lead_id = parts[1] if len(parts) > 1 else "unknown"

    user = message.from_user
    user_leads[user.id] = lead_id

    username = (
        f"@{user.username}"
        if user.username else "немає username"
    )

    text = (
        "🆕 <b>Новий лід!</b>\n\n"
        f"👤 Ім'я: {html.escape(user.full_name)}\n"
        f"📱 Username: {html.escape(username)}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Джерело: {html.escape(lead_id)}"
    )

    try:
        await bot.send_message(
            GROUP_CHAT_ID,
            text,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Failed to notify group")

    asyncio.create_task(send_lead(user, lead_id))

    if MAKE_BOT_WEBHOOK_URL:
        asyncio.create_task(
            asyncio.to_thread(
                post_bot_event,
                {
                    "event": "bot_start",
                    "lead_id": lead_id,
                    "telegram_id": user.id,
                    "username": (
                        f"@{user.username}"
                        if user.username else ""
                    ),
                    "full_name": user.full_name,
                    "timestamp": (
                        int(message.date.timestamp())
                        if message.date else None
                    ),
                },
            )
        )


# =========================================================
# /BROADCAST
# =========================================================

@dp.message(
    Command("broadcast"),
    F.chat.id == GROUP_CHAT_ID,
)
async def broadcast_command(message: types.Message):
    if broadcast_running:
        await message.reply(
            "⚠️ Розсилка вже виконується."
        )
        return

    user_id = message.from_user.id

    broadcast_waiting_for_text.add(user_id)
    pending_broadcasts.pop(user_id, None)

    logger.info(
        "Broadcast initiated by user %s",
        user_id,
    )

    await message.reply(
        "📢 <b>Нова розсилка</b>\n\n"
        "Надішли ОДНИМ повідомленням текст, "
        "який потрібно відправити всім клієнтам.\n\n"
        "Можна надсилати текст як звичайне "
        "повідомлення або як відповідь на це.\n\n"
        "Для скасування: /cancelbroadcast",
        parse_mode="HTML",
    )


# =========================================================
# /CANCELBROADCAST
# =========================================================

@dp.message(
    Command("cancelbroadcast"),
    F.chat.id == GROUP_CHAT_ID,
)
async def cancel_broadcast(message: types.Message):
    user_id = message.from_user.id

    broadcast_waiting_for_text.discard(user_id)
    pending_broadcasts.pop(user_id, None)

    await message.reply(
        "❌ Підготовку розсилки скасовано."
    )


# =========================================================
# ПРИЙОМ ТЕКСТУ РОЗСИЛКИ
# ВАЖЛИВО: СТОЇТЬ ПЕРЕД ОБРОБНИКОМ REPLY
# =========================================================

@dp.message(
    F.chat.id == GROUP_CHAT_ID,
    F.text,
)
async def capture_broadcast_text(message: types.Message):
    user_id = message.from_user.id

    if user_id not in broadcast_waiting_for_text:
        return

    if message.text.startswith("/"):
        return

    broadcast_waiting_for_text.discard(user_id)

    pending_broadcasts[user_id] = message.text

    logger.info(
        "Broadcast text captured from %s",
        user_id,
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✅ Розіслати",
                    callback_data=f"broadcast_confirm:{user_id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="❌ Скасувати",
                    callback_data=f"broadcast_cancel:{user_id}",
                )
            ],
        ]
    )

    preview = html.escape(message.text)

    await message.reply(
        "📢 <b>Попередній перегляд:</b>\n\n"
        f"{preview}\n\n"
        "⚠️ Це повідомлення буде відправлено "
        "ВСІМ користувачам із таблиці.\n\n"
        "Почати розсилку?",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# =========================================================
# СКАСУВАННЯ КНОПКОЮ
# =========================================================

@dp.callback_query(
    F.data.startswith("broadcast_cancel:")
)
async def broadcast_cancel_callback(
    callback: types.CallbackQuery,
):
    owner_id = int(callback.data.split(":")[1])

    if callback.from_user.id != owner_id:
        await callback.answer(
            "Це не ваша розсилка.",
            show_alert=True,
        )
        return

    pending_broadcasts.pop(owner_id, None)
    broadcast_waiting_for_text.discard(owner_id)

    await callback.answer()

    await callback.message.edit_text(
        "❌ Розсилку скасовано."
    )


# =========================================================
# ПІДТВЕРДЖЕННЯ ТА ВІДПРАВКА РОЗСИЛКИ
# =========================================================

@dp.callback_query(
    F.data.startswith("broadcast_confirm:")
)
async def broadcast_confirm_callback(
    callback: types.CallbackQuery,
):
    global broadcast_running

    owner_id = int(callback.data.split(":")[1])

    if callback.from_user.id != owner_id:
        await callback.answer(
            "Це не ваша розсилка.",
            show_alert=True,
        )
        return

    if broadcast_running:
        await callback.answer(
            "Розсилка вже виконується.",
            show_alert=True,
        )
        return

    broadcast_text = pending_broadcasts.get(owner_id)

    if not broadcast_text:
        await callback.answer(
            "Текст розсилки не знайдено.",
            show_alert=True,
        )
        return

    broadcast_running = True

    await callback.answer()

    sent = 0
    blocked = 0
    errors = 0
    total = 0

    try:
        await callback.message.edit_text(
            "⏳ Отримую список клієнтів із таблиці..."
        )

        users = await get_broadcast_users()

        if not users:
            await callback.message.edit_text(
                "❌ Список клієнтів порожній.\n"
                "Перевір Google Sheets та Make."
            )
            return

        total = len(users)

        logger.info(
            "Broadcast started: %s users",
            total,
        )

        await callback.message.edit_text(
            "🚀 <b>Розсилка почалась</b>\n\n"
            f"👥 Користувачів у базі: {total}",
            parse_mode="HTML",
        )

        # Видаляємо дублікати ID
        unique_ids = set()

        for user in users:
            if not isinstance(user, dict):
                errors += 1
                continue

            telegram_id = user.get("telegram_id")

            if not telegram_id:
                errors += 1
                continue

            try:
                client_id = int(telegram_id)
            except (ValueError, TypeError):
                errors += 1
                continue

            if client_id in unique_ids:
                continue

            unique_ids.add(client_id)

            try:
                await bot.send_message(
                    chat_id=client_id,
                    text=broadcast_text,
                )

                sent += 1

                await update_user_status(
                    client_id,
                    "✅ Активний",
                )

            except TelegramForbiddenError:
                blocked += 1

                await update_user_status(
                    client_id,
                    "🚫 Заблокував бота",
                )

            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)

                try:
                    await bot.send_message(
                        chat_id=client_id,
                        text=broadcast_text,
                    )

                    sent += 1

                    await update_user_status(
                        client_id,
                        "✅ Активний",
                    )

                except TelegramForbiddenError:
                    blocked += 1

                    await update_user_status(
                        client_id,
                        "🚫 Заблокував бота",
                    )

                except Exception:
                    errors += 1
                    logger.exception(
                        "Broadcast retry failed: %s",
                        client_id,
                    )

            except Exception:
                errors += 1

                logger.exception(
                    "Broadcast failed for %s",
                    client_id,
                )

            # Обмеження швидкості надсилання
            await asyncio.sleep(0.08)

        pending_broadcasts.pop(owner_id, None)

        report = (
            "✅ <b>Розсилку завершено!</b>\n\n"
            f"👥 Всього в базі: {total}\n"
            f"✅ Відправлено: {sent}\n"
            f"🚫 Заблокували бота: {blocked}\n"
            f"⚠️ Інші помилки: {errors}"
        )

        await bot.send_message(
            GROUP_CHAT_ID,
            report,
            parse_mode="HTML",
        )

        logger.info(
            "Broadcast finished: sent=%s blocked=%s errors=%s",
            sent,
            blocked,
            errors,
        )

    except Exception:
        logger.exception("Broadcast fatal error")

        try:
            await bot.send_message(
                GROUP_CHAT_ID,
                "❌ Помилка під час розсилки.\n"
                f"Успішно надіслано: {sent}\n"
                "Перевір Deploy Logs у Railway "
                "перед повторним запуском.",
            )
        except Exception:
            logger.exception("Cannot send error report")

    finally:
        broadcast_running = False


# =========================================================
# /REPLY ID ТЕКСТ
# =========================================================

@dp.message(
    Command("reply"),
    F.chat.id == GROUP_CHAT_ID,
)
async def reply_by_command(message: types.Message):
    args = (message.text or "").split(maxsplit=2)

    if len(args) < 3:
        await message.reply(
            "❌ Невірний формат.\n\n"
            "Використовуй:\n"
            "/reply user_id текст"
        )
        return

    try:
        client_id = int(args[1])
    except ValueError:
        await message.reply("❌ Невірний user_id")
        return

    text = args[2]

    try:
        await bot.send_message(
            chat_id=client_id,
            text=f"💬 Менеджер: {text}",
        )

        await message.reply(
            "✅ Відправлено клієнту"
        )

        asyncio.create_task(
            update_user_status(
                client_id,
                "✅ Активний",
            )
        )

    except TelegramForbiddenError:
        await message.reply(
            "🚫 Користувач заблокував бота."
        )

        asyncio.create_task(
            update_user_status(
                client_id,
                "🚫 Заблокував бота",
            )
        )

    except TelegramBadRequest as e:
        await message.reply(
            f"⚠️ Помилка Telegram:\n{e}"
        )

    except Exception:
        logger.exception("Reply command error")
        await message.reply(
            "❌ Помилка відправки. Перевір Railway."
        )


# =========================================================
# ВІДПОВІДЬ НА ПОВІДОМЛЕННЯ КЛІЄНТА
# =========================================================

@dp.message(
    F.chat.id == GROUP_CHAT_ID,
    F.reply_to_message,
)
async def reply_to_user(message: types.Message):
    if message.text and message.text.startswith("/"):
        return

    # Якщо менеджер готує розсилку,
    # це повідомлення не пересилаємо клієнту
    if message.from_user.id in broadcast_waiting_for_text:
        return

    replied_text = (
        message.reply_to_message.html_text
        or message.reply_to_message.text
        or ""
    )

    match = re.search(
        r"<code>(\d+)</code>",
        replied_text,
    )

    if not match:
        match = re.search(
            r"ID:\s*(\d+)",
            replied_text,
        )

    if not match:
        # Не відповідаємо на звичайні
        # повідомлення бота без ID клієнта
        return

    client_id = int(match.group(1))

    try:
        if message.text:
            await bot.send_message(
                chat_id=client_id,
                text=f"💬 Менеджер: {message.text}",
            )

        elif any([
            message.document,
            message.photo,
            message.video,
            message.voice,
            message.audio,
            message.animation,
            message.sticker,
            message.video_note,
        ]):
            await bot.copy_message(
                chat_id=client_id,
                from_chat_id=GROUP_CHAT_ID,
                message_id=message.message_id,
            )

        else:
            await message.reply(
                "⚠️ Цей тип повідомлення не підтримується."
            )
            return

        await message.reply(
            "✅ Відправлено клієнту"
        )

        asyncio.create_task(
            update_user_status(
                client_id,
                "✅ Активний",
            )
        )

    except TelegramForbiddenError:
        await message.reply(
            "🚫 Користувач заблокував бота."
        )

        asyncio.create_task(
            update_user_status(
                client_id,
                "🚫 Заблокував бота",
            )
        )

    except TelegramBadRequest as e:
        await message.reply(
            f"⚠️ Не вдалося відправити:\n{e}"
        )

    except Exception:
        logger.exception("Group reply error")
        await message.reply(
            "❌ Помилка. Перевір Railway."
        )


# =========================================================
# ПОВІДОМЛЕННЯ КЛІЄНТА -> РОБОЧА ГРУПА
# =========================================================

@dp.message(F.chat.type == "private")
async def forward_to_group(message: types.Message):
    if message.text and message.text.startswith("/"):
        return

    user = message.from_user

    username = (
        f"@{user.username}"
        if user.username else "немає username"
    )

    content = extract_message_content(message)

    lead_id = user_leads.get(
        user.id,
        "unknown",
    )

    text = (
        "💬 <b>Повідомлення від клієнта</b>\n"
        f"👤 {html.escape(user.full_name)} "
        f"({html.escape(username)})\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Lead ID: "
        f"<code>{html.escape(lead_id)}</code>\n\n"
        f"➡️ {html.escape(content)}"
    )

    try:
        await bot.send_message(
            GROUP_CHAT_ID,
            text,
            parse_mode="HTML",
        )

        if any([
            message.document,
            message.photo,
            message.video,
            message.voice,
            message.audio,
            message.sticker,
            message.video_note,
            message.animation,
        ]):
            await bot.forward_message(
                chat_id=GROUP_CHAT_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )

    except Exception:
        logger.exception("Forward to group error")


# =========================================================
# /RESTART
# =========================================================

@dp.message(Command("restart"))
async def restart_cmd(message: types.Message):
    await show_chat_cta(message)


# =========================================================
# /CONTACTS
# =========================================================

@dp.message(Command("contacts"))
async def contacts_cmd(message: types.Message):
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✉️ Написати менеджеру",
                    url=f"https://t.me/{MANAGER_USERNAME}",
                )
            ]
        ]
    )

    await message.answer(
        "📞 <b>Контакти Easy.Five</b>\n\n"
        "Менеджер відповість найближчим часом 👇",
        parse_mode="HTML",
        reply_markup=kb,
    )


# =========================================================
# ЗАПУСК
# =========================================================

async def main():
    logger.info("Easy.Five bot started")

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(main())
