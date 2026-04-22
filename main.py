import asyncio
import os
import urllib.parse
import requests
import re

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command

BOT_TOKEN = os.getenv("BOT_TOKEN")
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME")
LEADS_WEBHOOK_URL = os.getenv("LEADS_WEBHOOK_URL")
MAKE_BOT_WEBHOOK_URL = os.getenv("MAKE_BOT_WEBHOOK_URL")

GROUP_CHAT_ID = -5233088810  # ваша група менеджерів

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing.")
if not MANAGER_USERNAME:
    raise ValueError("MANAGER_USERNAME is missing.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Зв'язка telegram user -> lead_id
user_leads: dict[int, str] = {}


def manager_link(text: str) -> str:
    return f"https://t.me/{MANAGER_USERNAME}?text={urllib.parse.quote(text)}"


def _post_lead(payload: dict) -> None:
    if not LEADS_WEBHOOK_URL:
        return
    try:
        requests.post(LEADS_WEBHOOK_URL, json=payload, timeout=8)
    except Exception:
        pass


def _post_bot_event(payload: dict) -> None:
    if not MAKE_BOT_WEBHOOK_URL:
        return
    try:
        requests.post(MAKE_BOT_WEBHOOK_URL, json=payload, timeout=8)
    except Exception:
        pass


async def send_lead(user: types.User, source: str) -> None:
    payload = {
        "telegram_id": user.id,
        "username": f"@{user.username}" if user.username else "",
        "full_name": user.full_name,
        "source": source,
    }
    await asyncio.to_thread(_post_lead, payload)


async def send_bot_start_event(payload: dict) -> None:
    await asyncio.to_thread(_post_bot_event, payload)


def main_buttons() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Дізнатися вартість моєї роботи 📚",
                    url=manager_link("Я хочу дізнатись вартість моєї роботи!"),
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


async def show_chat_cta(message: types.Message) -> None:
    await message.answer(
        "Щоб зв'язатись з нашим менеджером, натискай кнопку нижче 👇🏼",
        reply_markup=main_buttons(),
    )


def extract_message_content(message: types.Message) -> str:
    if message.text:
        return message.text

    if message.document:
        file_name = message.document.file_name or "без назви"
        caption = f"\nПідпис: {message.caption}" if message.caption else ""
        return f"[Документ: {file_name}]{caption}"

    if message.photo:
        caption = f"\nПідпис: {message.caption}" if message.caption else ""
        return f"[Фото]{caption}"

    if message.video:
        caption = f"\nПідпис: {message.caption}" if message.caption else ""
        return f"[Відео]{caption}"

    if message.voice:
        return "[Голосове повідомлення]"

    if message.audio:
        title = message.audio.title or message.audio.file_name or "без назви"
        caption = f"\nПідпис: {message.caption}" if message.caption else ""
        return f"[Аудіо: {title}]{caption}"

    if message.sticker:
        emoji = f" {message.sticker.emoji}" if message.sticker.emoji else ""
        return f"[Стікер{emoji}]"

    if message.video_note:
        return "[Відеоповідомлення]"

    if message.animation:
        caption = f"\nПідпис: {message.caption}" if message.caption else ""
        return f"[GIF / анімація]{caption}"

    return "[Невідомий тип повідомлення]"


@dp.message(CommandStart())
async def start(message: types.Message) -> None:
    await show_chat_cta(message)

    parts = (message.text or "").split(maxsplit=1)
    lead_id = parts[1] if len(parts) > 1 else "unknown"

    user = message.from_user
    user_leads[user.id] = lead_id

    username = f"@{user.username}" if user.username else "немає username"
    text = (
        f"🆕 <b>Новий лід!</b>\n\n"
        f"👤 Ім'я: {user.full_name}\n"
        f"📱 Username: {username}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Джерело: {lead_id}"
    )

    await bot.send_message(GROUP_CHAT_ID, text, parse_mode="HTML")

    if LEADS_WEBHOOK_URL:
        asyncio.create_task(send_lead(user, lead_id))

    if MAKE_BOT_WEBHOOK_URL:
        asyncio.create_task(send_bot_start_event({
            "event": "bot_start",
            "lead_id": lead_id,
            "telegram_id": user.id,
            "username": f"@{user.username}" if user.username else "",
            "full_name": user.full_name,
            "timestamp": int(message.date.timestamp()) if message.date else None,
        }))


@dp.message(F.chat.type == "private")
async def forward_to_group(message: types.Message) -> None:
    if message.text and message.text.startswith("/"):
        return

    user = message.from_user
    username = f"@{user.username}" if user.username else "немає username"
    content = extract_message_content(message)
    lead_id = user_leads.get(user.id, "unknown")

    text = (
        f"💬 <b>Повідомлення від клієнта</b>\n"
        f"👤 {user.full_name} ({username})\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Lead ID: <code>{lead_id}</code>\n\n"
        f"➡️ {content}"
    )

    await bot.send_message(GROUP_CHAT_ID, text, parse_mode="HTML")

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


@dp.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message)
async def reply_to_user(message: types.Message) -> None:
    replied_text = message.reply_to_message.html_text or message.reply_to_message.text or ""

    match = re.search(r"<code>(\d+)</code>", replied_text)
    if not match:
        match = re.search(r"ID:\s*(\d+)", replied_text)

    if not match:
        return

    client_id = int(match.group(1))

    if message.text:
        await bot.send_message(client_id, f"💬 Менеджер: {message.text}")
    elif message.document or message.photo or message.video or message.voice or message.audio:
        await bot.copy_message(
            chat_id=client_id,
            from_chat_id=GROUP_CHAT_ID,
            message_id=message.message_id,
        )


@dp.message(Command("reply"))
async def reply_by_command(message: types.Message) -> None:
    if message.chat.id != GROUP_CHAT_ID:
        return

    args = (message.text or "").split(maxsplit=2)

    if len(args) < 3:
        await message.reply("Формат: /reply user_id текст")
        return

    try:
        client_id = int(args[1])
    except ValueError:
        await message.reply("❌ Невірний user_id")
        return

    text = args[2]

    try:
        await bot.send_message(client_id, f"💬 Менеджер: {text}")
        await message.reply("✅ Відправлено клієнту")
    except Exception as e:
        await message.reply(f"❌ Помилка: {e}")


@dp.message(Command("restart"))
async def restart_cmd(message: types.Message) -> None:
    await show_chat_cta(message)


@dp.message(Command("contacts"))
async def contacts_cmd(message: types.Message) -> None:
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[[
            types.InlineKeyboardButton(
                text="✉️ Написати менеджеру",
                url=f"https://t.me/{MANAGER_USERNAME}",
            )
        ]]
    )
    await message.answer(
        "📞 <b>Контакти Easy.Five</b>\n\nМенеджер відповість найближчим часом 👇",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def main() -> None:
    print("🤖 Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
