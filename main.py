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
GROUP_CHAT_ID = -5233088810  # ваша група менеджерів

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing.")
if not MANAGER_USERNAME:
    raise ValueError("MANAGER_USERNAME is missing.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Словник: user_id -> message_id в групі
user_message_map = {}

def manager_link(text: str) -> str:
    return f"https://t.me/{MANAGER_USERNAME}?text={urllib.parse.quote(text)}"

def _post_lead(payload: dict) -> None:
    if not LEADS_WEBHOOK_URL:
        return
    try:
        requests.post(LEADS_WEBHOOK_URL, json=payload, timeout=8)
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

@dp.message(CommandStart())
async def start(message: types.Message) -> None:
    await show_chat_cta(message)

    parts = (message.text or "").split(maxsplit=1)
    source = parts[1] if len(parts) > 1 else "unknown"

    user = message.from_user
    username = f"@{user.username}" if user.username else "немає username"
    text = (
        f"🆕 <b>Новий лід!</b>\n\n"
        f"👤 Ім'я: {user.full_name}\n"
        f"📱 Username: {username}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Джерело: {source}"
    )
    sent = await bot.send_message(GROUP_CHAT_ID, text, parse_mode="HTML")
    user_message_map[user.id] = sent.message_id

    if LEADS_WEBHOOK_URL:
        asyncio.create_task(send_lead(user, source))

@dp.message(F.chat.type == "private")
async def forward_to_group(message: types.Message) -> None:
    user = message.from_user
    username = f"@{user.username}" if user.username else "немає username"
    text = (
        f"💬 <b>Повідомлення від клієнта</b>\n"
        f"👤 {user.full_name} ({username})\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"➡️ {message.text or '[не текст]'}"
    )
    await bot.send_message(GROUP_CHAT_ID, text, parse_mode="HTML")

@dp.message(F.chat.id == GROUP_CHAT_ID, F.reply_to_message)
async def reply_to_user(message: types.Message) -> None:
    replied_text = message.reply_to_message.text or ""

    match = re.search(r"ID:\s*(\d+)", replied_text)
    if not match:
        match = re.search(r"<code>(\d+)</code>", replied_text)

    if not match:
        return

    client_id = int(match.group(1))
    await bot.send_message(client_id, f"💬 Менеджер: {message.text}")

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
