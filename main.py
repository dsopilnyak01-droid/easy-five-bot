import asyncio
import os
import urllib.parse
import requests

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command

# =============================
# 1) Environment variables (Railway / Replit)
# =============================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME")  # without @, e.g. easy_five05
LEADS_WEBHOOK_URL = os.getenv("LEADS_WEBHOOK_URL")  # optional (Google Apps Script Web App URL)

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing. Add it in Railway → Variables.")
if not MANAGER_USERNAME:
    raise ValueError("MANAGER_USERNAME is missing. Add it in Railway → Variables (without @).")

# =============================
# 2) Init bot
# =============================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# =============================
# 3) Manager deep-link with prefilled text
# =============================
def manager_link(text: str) -> str:
    # Telegram supports prefilled text via ?text=
    return f"https://t.me/{MANAGER_USERNAME}?text={urllib.parse.quote(text)}"

# =============================
# 4) Send lead to Google Sheets (non-blocking)
# =============================
def _post_lead(payload: dict) -> None:
    if not LEADS_WEBHOOK_URL:
        return
    try:
        requests.post(LEADS_WEBHOOK_URL, json=payload, timeout=8)
    except Exception:
        # don't crash the bot because of lead webhook issues
        pass

async def send_lead(user: types.User, source: str) -> None:
    payload = {
        "telegram_id": user.id,
        "username": f"@{user.username}" if user.username else "",
        "full_name": user.full_name,
        "source": source,
    }
    await asyncio.to_thread(_post_lead, payload)

# =============================
# 5) Main buttons (1 click -> opens manager chat)
# =============================
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

# =============================
# 6) Helper: CTA + buttons
# =============================
async def show_chat_cta(message: types.Message) -> None:
    await message.answer(
        "Щоб зв'язатись з нашим менеджером, натискай кнопку нижче 👇🏼",
        reply_markup=main_buttons(),
    )

# =============================
# 7) /start
# =============================
@dp.message(CommandStart())
async def start(message: types.Message) -> None:
    await show_chat_cta(message)

    # /start <source>
    parts = (message.text or "").split(maxsplit=1)
    source = parts[1] if len(parts) > 1 else "unknown"

    # send lead in background (don't block reply)
    if LEADS_WEBHOOK_URL:
        asyncio.create_task(send_lead(message.from_user, source))

# =============================
# 8) Menu commands
# =============================
@dp.message(Command("restart"))
async def restart_cmd(message: types.Message) -> None:
    await show_chat_cta(message)

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

@dp.message(Command("about"))
async def about_cmd(message: types.Message) -> None:
    about_text = (
        "Easy.Five — це сервіс професійної допомоги студентам 👩🏽‍💻\n\n"
        "🔸 Понад 12 000 виконаних студентських робіт\n"
        "🔸 Команда досвідчених авторів і викладачів\n"
        "🔸 Працюємо з 90+ спеціальностями\n"
        "🔸 Курсові, дипломні, магістерські, реферати та інші роботи\n"
        "🔸 Індивідуальне виконання без шаблонів\n"
        "🔸 Перевірка на антиплагіат + звіт для клієнта\n"
        "🔸 Безкоштовні правки в межах початкових вимог\n"
        "🔸 Персональний менеджер на всіх етапах\n"
        "🔸 Конфіденційність\n\n"
        "Easy.Five — коли навчання стає простішим, а результат — впевненим!"
    )

    await message.answer(about_text)
    await show_chat_cta(message)

# =============================
# 9) Run bot
# =============================
async def main() -> None:
    print("🤖 Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
