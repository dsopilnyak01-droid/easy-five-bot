import asyncio
import os
import urllib.parse
import requests
import re

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter


BOT_TOKEN = os.getenv("BOT_TOKEN")
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME")
LEADS_WEBHOOK_URL = os.getenv("LEADS_WEBHOOK_URL")
MAKE_BOT_WEBHOOK_URL = os.getenv("MAKE_BOT_WEBHOOK_URL")

GROUP_CHAT_ID = -5233088810  # ваша група менеджерів


if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing.")

if not MANAGER_USERNAME:
    raise ValueError("MANAGER_USERNAME is missing.")

if not LEADS_WEBHOOK_URL:
    raise ValueError("LEADS_WEBHOOK_URL is missing.")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# ДАНІ
# =========================================================

# Зв'язка telegram user -> lead_id
user_leads: dict[int, str] = {}

# Хто зараз створює broadcast
broadcast_waiting_for_text: set[int] = set()

# Збережені повідомлення перед підтвердженням
pending_broadcasts: dict[int, str] = {}

# Захист від запуску двох розсилок одночасно
broadcast_running = False


# =========================================================
# GOOGLE SHEETS API
# =========================================================

def _post_lead(payload: dict) -> None:
    if not LEADS_WEBHOOK_URL:
        return

    try:
        requests.post(
            LEADS_WEBHOOK_URL,
            json=payload,
            timeout=15
        )
    except Exception:
        pass


def _post_bot_event(payload: dict) -> None:
    if not MAKE_BOT_WEBHOOK_URL:
        return

    try:
        requests.post(
            MAKE_BOT_WEBHOOK_URL,
            json=payload,
            timeout=8
        )
    except Exception:
        pass


def _get_broadcast_users():
    try:
        response = requests.post(
            LEADS_WEBHOOK_URL,
            json={
                "action": "get_users"
            },
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        if data.get("status") != "ok":
            return []

        return data.get("users", [])

    except Exception as e:
        print("Get users error:", e)
        return []


def _update_user_status(telegram_id, status):
    try:
        requests.post(
            LEADS_WEBHOOK_URL,
            json={
                "action": "update_status",
                "telegram_id": str(telegram_id),
                "bot_status": status
            },
            timeout=15
        )
    except Exception as e:
        print("Update status error:", e)


async def get_broadcast_users():
    return await asyncio.to_thread(_get_broadcast_users)


async def update_user_status(telegram_id, status):
    await asyncio.to_thread(
        _update_user_status,
        telegram_id,
        status
    )


# =========================================================
# СТАРА ЛОГІКА
# =========================================================

def manager_link(text: str) -> str:
    return (
        f"https://t.me/{MANAGER_USERNAME}"
        f"?text={urllib.parse.quote(text)}"
    )


async def send_lead(user: types.User, source: str) -> None:

    payload = {
        "telegram_id": user.id,
        "username": f"@{user.username}" if user.username else "",
        "full_name": user.full_name,
        "source": source,
    }

    await asyncio.to_thread(
        _post_lead,
        payload
    )


async def send_bot_start_event(payload: dict) -> None:
    await asyncio.to_thread(
        _post_bot_event,
        payload
    )


def main_buttons() -> types.InlineKeyboardMarkup:

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
                    url=manager_link(
                        "Я з Instagram"
                    ),
                )
            ],
        ]
    )


async def show_chat_cta(message: types.Message) -> None:

    await message.answer(
        "Щоб зв'язатись з нашим менеджером, "
        "натискай кнопку нижче 👇🏼",
        reply_markup=main_buttons(),
    )


def extract_message_content(message: types.Message) -> str:

    if message.text:
        return message.text

    if message.document:
        file_name = message.document.file_name or "без назви"
        caption = (
            f"\nПідпис: {message.caption}"
            if message.caption else ""
        )
        return f"[Документ: {file_name}]{caption}"

    if message.photo:
        caption = (
            f"\nПідпис: {message.caption}"
            if message.caption else ""
        )
        return f"[Фото]{caption}"

    if message.video:
        caption = (
            f"\nПідпис: {message.caption}"
            if message.caption else ""
        )
        return f"[Відео]{caption}"

    if message.voice:
        return "[Голосове повідомлення]"

    if message.audio:
        title = (
            message.audio.title
            or message.audio.file_name
            or "без назви"
        )

        caption = (
            f"\nПідпис: {message.caption}"
            if message.caption else ""
        )

        return f"[Аудіо: {title}]{caption}"

    if message.sticker:
        emoji = (
            f" {message.sticker.emoji}"
            if message.sticker.emoji
            else ""
        )

        return f"[Стікер{emoji}]"

    if message.video_note:
        return "[Відеоповідомлення]"

    if message.animation:
        caption = (
            f"\nПідпис: {message.caption}"
            if message.caption else ""
        )

        return f"[GIF / анімація]{caption}"

    return "[Невідомий тип повідомлення]"


# =========================================================
# /START
# =========================================================

@dp.message(CommandStart())
async def start(message: types.Message) -> None:

    await show_chat_cta(message)

    parts = (message.text or "").split(maxsplit=1)

    lead_id = (
        parts[1]
        if len(parts) > 1
        else "unknown"
    )

    user = message.from_user

    user_leads[user.id] = lead_id

    username = (
        f"@{user.username}"
        if user.username
        else "немає username"
    )

    text = (
        f"🆕 <b>Новий лід!</b>\n\n"
        f"👤 Ім'я: {user.full_name}\n"
        f"📱 Username: {username}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Джерело: {lead_id}"
    )

    await bot.send_message(
        GROUP_CHAT_ID,
        text,
        parse_mode="HTML"
    )

    if LEADS_WEBHOOK_URL:
        asyncio.create_task(
            send_lead(
                user,
                lead_id
            )
        )

    if MAKE_BOT_WEBHOOK_URL:
        asyncio.create_task(
            send_bot_start_event({
                "event": "bot_start",
                "lead_id": lead_id,
                "telegram_id": user.id,
                "username":
                    f"@{user.username}"
                    if user.username
                    else "",
                "full_name": user.full_name,
                "timestamp":
                    int(message.date.timestamp())
                    if message.date
                    else None,
            })
        )


# =========================================================
# BROADCAST
# =========================================================

@dp.message(Command("broadcast"))
async def broadcast_command(message: types.Message) -> None:

    if message.chat.id != GROUP_CHAT_ID:
        return

    global broadcast_running

    if broadcast_running:
        await message.reply(
            "⚠️ Розсилка вже виконується."
        )
        return

    broadcast_waiting_for_text.add(
        message.from_user.id
    )

    await message.reply(
        "📢 <b>Нова розсилка</b>\n\n"
        "Тепер надішли ОДНИМ повідомленням текст, "
        "який потрібно відправити всім клієнтам.\n\n"
        "Для скасування: /cancelbroadcast",
        parse_mode="HTML"
    )


@dp.message(Command("cancelbroadcast"))
async def cancel_broadcast(message: types.Message) -> None:

    if message.chat.id != GROUP_CHAT_ID:
        return

    user_id = message.from_user.id

    broadcast_waiting_for_text.discard(user_id)

    if user_id in pending_broadcasts:
        del pending_broadcasts[user_id]

    await message.reply(
        "❌ Розсилку скасовано."
    )


@dp.message(
    F.chat.id == GROUP_CHAT_ID,
    F.text
)
async def capture_broadcast_text(message: types.Message) -> None:

    user_id = message.from_user.id

    if user_id not in broadcast_waiting_for_text:
        return

    if message.text.startswith("/"):
        return

    broadcast_waiting_for_text.discard(user_id)

    pending_broadcasts[user_id] = message.text

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✅ Розіслати",
                    callback_data=f"broadcast_confirm:{user_id}"
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="❌ Скасувати",
                    callback_data=f"broadcast_cancel:{user_id}"
                )
            ]
        ]
    )

    await message.reply(
        "📢 <b>Попередній перегляд:</b>\n\n"
        f"{message.text}\n\n"
        "⚠️ Це повідомлення буде відправлено "
        "ВСІМ користувачам із таблиці.\n\n"
        "Почати розсилку?",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@dp.callback_query(
    F.data.startswith("broadcast_cancel:")
)
async def broadcast_cancel_callback(
    callback: types.CallbackQuery
) -> None:

    owner_id = int(
        callback.data.split(":")[1]
    )

    if callback.from_user.id != owner_id:
        await callback.answer(
            "Це не ваша розсилка.",
            show_alert=True
        )
        return

    pending_broadcasts.pop(
        owner_id,
        None
    )

    await callback.message.edit_text(
        "❌ Розсилку скасовано."
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("broadcast_confirm:")
)
async def broadcast_confirm_callback(
    callback: types.CallbackQuery
) -> None:

    global broadcast_running

    owner_id = int(
        callback.data.split(":")[1]
    )

    if callback.from_user.id != owner_id:
        await callback.answer(
            "Це не ваша розсилка.",
            show_alert=True
        )
        return

    if broadcast_running:
        await callback.answer(
            "Розсилка вже виконується.",
            show_alert=True
        )
        return

    broadcast_text = pending_broadcasts.get(
        owner_id
    )

    if not broadcast_text:
        await callback.answer(
            "Текст розсилки не знайдено.",
            show_alert=True
        )
        return

    broadcast_running = True

    await callback.answer()

    await callback.message.edit_text(
        "⏳ Отримую список клієнтів із таблиці..."
    )

    try:

        users = await get_broadcast_users()

        if not users:

            await callback.message.edit_text(
                "❌ Не вдалося отримати користувачів "
                "із Google Sheets."
            )

            return

        total = len(users)

        await callback.message.edit_text(
            f"🚀 Розсилка почалась.\n\n"
            f"Користувачів у базі: {total}"
        )

        sent = 0
        blocked = 0
        errors = 0

        for user in users:

            telegram_id = user.get(
                "telegram_id"
            )

            if not telegram_id:
                continue

            try:

                await bot.send_message(
                    chat_id=int(telegram_id),
                    text=broadcast_text
                )

                sent += 1

                await update_user_status(
                    telegram_id,
                    "✅ Активний"
                )

            except TelegramForbiddenError:

                blocked += 1

                await update_user_status(
                    telegram_id,
                    "🚫 Заблокував бота"
                )

            except TelegramRetryAfter as e:

                await asyncio.sleep(
                    e.retry_after + 1
                )

                try:

                    await bot.send_message(
                        chat_id=int(telegram_id),
                        text=broadcast_text
                    )

                    sent += 1

                    await update_user_status(
                        telegram_id,
                        "✅ Активний"
                    )

                except TelegramForbiddenError:

                    blocked += 1

                    await update_user_status(
                        telegram_id,
                        "🚫 Заблокував бота"
                    )

                except Exception:

                    errors += 1

                    await update_user_status(
                        telegram_id,
                        "⚠️ Помилка"
                    )

            except TelegramBadRequest:

                errors += 1

                await update_user_status(
                    telegram_id,
                    "⚠️ Недоступний"
                )

            except Exception as e:

                print(
                    f"Broadcast error {telegram_id}:",
                    e
                )

                errors += 1

                await update_user_status(
                    telegram_id,
                    "⚠️ Помилка"
                )

            # Не шлемо сотні повідомлень одночасно
            await asyncio.sleep(0.08)


        pending_broadcasts.pop(
            owner_id,
            None
        )

        await bot.send_message(
            GROUP_CHAT_ID,
            "✅ <b>Розсилку завершено!</b>\n\n"
            f"👥 Всього в базі: {total}\n"
            f"✅ Відправлено: {sent}\n"
            f"🚫 Заблокували бота: {blocked}\n"
            f"⚠️ Інші помилки: {errors}",
            parse_mode="HTML"
        )

    except Exception as e:

        print(
            "Broadcast fatal error:",
            e
        )

        await bot.send_message(
            GROUP_CHAT_ID,
            f"❌ Помилка розсилки:\n{e}"
        )

    finally:

        broadcast_running = False


# =========================================================
# ПОВІДОМЛЕННЯ ВІД КЛІЄНТІВ
# =========================================================

@dp.message(F.chat.type == "private")
async def forward_to_group(message: types.Message) -> None:

    if message.text and message.text.startswith("/"):
        return

    user = message.from_user

    username = (
        f"@{user.username}"
        if user.username
        else "немає username"
    )

    content = extract_message_content(
        message
    )

    lead_id = user_leads.get(
        user.id,
        "unknown"
    )

    text = (
        f"💬 <b>Повідомлення від клієнта</b>\n"
        f"👤 {user.full_name} ({username})\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Lead ID: <code>{lead_id}</code>\n\n"
        f"➡️ {content}"
    )

    await bot.send_message(
        GROUP_CHAT_ID,
        text,
        parse_mode="HTML"
    )

    if (
        message.document
        or message.photo
        or message.video
        or message.voice
        or message.audio
        or message.sticker
        or message.video_note
        or message.animation
    ):

        await bot.forward_message(
            chat_id=GROUP_CHAT_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )


# =========================================================
# REPLY У ГРУПІ
# =========================================================

@dp.message(
    F.chat.id == GROUP_CHAT_ID,
    F.reply_to_message
)
async def reply_to_user(message: types.Message) -> None:

    replied_text = (
        message.reply_to_message.html_text
        or message.reply_to_message.text
        or ""
    )

    match = re.search(
        r"<code>(\d+)</code>",
        replied_text
    )

    if not match:

        match = re.search(
            r"ID:\s*(\d+)",
            replied_text
        )

    if not match:
        return

    client_id = int(
        match.group(1)
    )

    if message.text:

        await bot.send_message(
            client_id,
            f"💬 Менеджер: {message.text}"
        )

    elif (
        message.document
        or message.photo
        or message.video
        or message.voice
        or message.audio
    ):

        await bot.copy_message(
            chat_id=client_id,
            from_chat_id=GROUP_CHAT_ID,
            message_id=message.message_id,
        )


# =========================================================
# /REPLY
# =========================================================

@dp.message(Command("reply"))
async def reply_by_command(message: types.Message) -> None:

    if message.chat.id != GROUP_CHAT_ID:
        return

    args = (
        message.text or ""
    ).split(maxsplit=2)

    if len(args) < 3:

        await message.reply(
            "Формат: /reply user_id текст"
        )

        return

    try:

        client_id = int(
            args[1]
        )

    except ValueError:

        await message.reply(
            "❌ Невірний user_id"
        )

        return

    text = args[2]

    try:

        await bot.send_message(
            client_id,
            f"💬 Менеджер: {text}"
        )

        await message.reply(
            "✅ Відправлено клієнту"
        )

    except Exception as e:

        await message.reply(
            f"❌ Помилка: {e}"
        )


# =========================================================
# ІНШІ КОМАНДИ
# =========================================================

@dp.message(Command("restart"))
async def restart_cmd(message: types.Message) -> None:

    await show_chat_cta(
        message
    )


@dp.message(Command("contacts"))
async def contacts_cmd(message: types.Message) -> None:

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
# START BOT
# =========================================================

async def main() -> None:

    print("🤖 Bot started")

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(main())
