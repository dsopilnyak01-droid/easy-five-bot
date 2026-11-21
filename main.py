import asyncio
import os
import urllib.parse
import requests
import re
import html

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

BOT_TOKEN = os.getenv("BOT_TOKEN")
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME")
LEADS_WEBHOOK_URL = os.getenv("LEADS_WEBHOOK_URL")
MAKE_BOT_WEBHOOK_URL = os.getenv("MAKE_BOT_WEBHOOK_URL")

GROUP_CHAT_ID = -5233088810


if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing.")

if not MANAGER_USERNAME:
    raise ValueError("MANAGER_USERNAME is missing.")

if not LEADS_WEBHOOK_URL:
    raise ValueError("LEADS_WEBHOOK_URL is missing.")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# ТИМЧАСОВІ ДАНІ
# =========================================================

# telegram user -> lead_id
user_leads: dict[int, str] = {}

# Хто зараз створює розсилку
broadcast_waiting_for_text: set[int] = set()

# Текст майбутньої розсилки
pending_broadcasts: dict[int, str] = {}

# Захист від двох одночасних розсилок
broadcast_running = False


# =========================================================
# GOOGLE SHEETS
# =========================================================

def _post_lead(payload: dict) -> None:
    try:
        requests.post(
            LEADS_WEBHOOK_URL,
            json=payload,
            timeout=15,
        )
    except Exception as e:
        print("Lead webhook error:", e)


def _post_bot_event(payload: dict) -> None:
    if not MAKE_BOT_WEBHOOK_URL:
        return

    try:
        requests.post(
            MAKE_BOT_WEBHOOK_URL,
            json=payload,
            timeout=8,
        )
    except Exception as e:
        print("Bot event error:", e)


def _get_broadcast_users():
    try:
        response = requests.post(
            LEADS_WEBHOOK_URL,
            json={
                "action": "get_users"
            },
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        if data.get("status") != "ok":
            return []

        return data.get("users", [])

    except Exception as e:
        print("Get users error:", e)
        return []


def _update_user_status(
    telegram_id,
    status,
):
    try:
        requests.post(
            LEADS_WEBHOOK_URL,
            json={
                "action": "update_status",
                "telegram_id": str(telegram_id),
                "bot_status": status,
            },
            timeout=15,
        )

    except Exception as e:
        print(
            "Update status error:",
            e
        )


async def get_broadcast_users():
    return await asyncio.to_thread(
        _get_broadcast_users
    )


async def update_user_status(
    telegram_id,
    status,
):
    await asyncio.to_thread(
        _update_user_status,
        telegram_id,
        status,
    )


# =========================================================
# ПОСИЛАННЯ НА МЕНЕДЖЕРА
# =========================================================

def manager_link(text: str) -> str:
    return (
        f"https://t.me/{MANAGER_USERNAME}"
        f"?text={urllib.parse.quote(text)}"
    )


# =========================================================
# ЗБЕРЕЖЕННЯ ЛІДА
# =========================================================

async def send_lead(
    user: types.User,
    source: str,
) -> None:

    payload = {
        "telegram_id": user.id,
        "username":
            f"@{user.username}"
            if user.username
            else "",
        "full_name": user.full_name,
        "source": source,
    }

    await asyncio.to_thread(
        _post_lead,
        payload,
    )


async def send_bot_start_event(
    payload: dict
) -> None:

    await asyncio.to_thread(
        _post_bot_event,
        payload,
    )


# =========================================================
# КНОПКИ
# =========================================================

def main_buttons(
    price_text: str = "Я хочу дізнатись вартість моєї роботи!",
) -> types.InlineKeyboardMarkup:

    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="Дізнатися вартість моєї роботи 📚",
                    url=manager_link(
                        price_text
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


# Окрема коротка воронка для рекламних переходів Instagram.
# Дозволяємо декілька оголошень: instagram_ad1, instagram_ad2 тощо.
# Звичайний /start та site-* залишаються без змін.
def is_instagram_ad(lead_id: str) -> bool:
    return bool(re.fullmatch(r"instagram_ad[a-zA-Z0-9_]*", lead_id))


async def show_instagram_ad_cta(
    message: types.Message,
    lead_id: str,
) -> None:
    # URL-кнопка відкриває особистий чат менеджера одним натисканням.
    # Telegram Bot API не надсилає callback для URL-кнопок,
    # тому натискання не можна достовірно рахувати без редиректу.
    text = (
        "Вітаю!👋🏼\n\n"
        "Завжди раді допомогти тобі з будь-яким навчальним завданням!📚\n\n"
        "Що саме тебе цікавить?✨"
    )
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[[
            types.InlineKeyboardButton(
                text="💬 Написати менеджеру",
                url=manager_link("Привіт! Я з реклами Easy.Five в Instagram. Хочу проконсультуватися 🙂"),
            )
        ]]
    )
    await message.answer(text, reply_markup=kb)


async def show_chat_cta(
    message: types.Message
) -> None:

    await message.answer(
        "Щоб зв'язатись з нашим менеджером, "
        "натискай кнопку нижче 👇🏼",
        reply_markup=main_buttons(),
    )


# =========================================================
# ЛІДИ ІЗ САЙТУ
#
# Сайт відкриває бота посиланням:
#   t.me/Easy_Five_bot?start=site-<тип>-<дедлайн>
# наприклад: start=site-magisterska-tyzhden
# =========================================================

# Як лід із сайту підписується в таблиці та в групі
SITE_SOURCE = "Сайт Оля"

SITE_WORK_TYPES = {
    # код: (назва, "вартість чого?")
    "kursova":       ("Курсова",          "курсової роботи"),
    "dyplomna":      ("Дипломна",         "дипломної роботи"),
    "magisterska":   ("Магістерська",     "магістерської роботи"),
    "referat":       ("Реферат",          "реферату"),
    "prezentatsiia": ("Презентація",      "презентації"),
    "ese":           ("Есе",              "есе"),
    "tezy":          ("Тези",             "тез"),
    "indz":          ("ІНДЗ",             "ІНДЗ"),
    "praktychna":    ("Практична робота", "практичної роботи"),
    "zvit":          ("Звіт з практики",  "звіту з практики"),
    "insha":         ("Інша робота",      "моєї роботи"),
}

SITE_DEADLINES = {
    "terminovo": "Терміново (1–2 дні)",
    "tyzhden":   "Тиждень (3–7 днів)",
    "nehoryt":   "Не горить (7+ днів)",
}


def parse_site_lead(lead_id: str) -> dict | None:
    """Повертає дані ліда із сайту або None, якщо лід не із сайту."""

    parts = lead_id.split("-")

    if parts[0] != "site":
        return None

    work = SITE_WORK_TYPES.get(
        parts[1] if len(parts) > 1 else ""
    )
    deadline = SITE_DEADLINES.get(
        parts[2] if len(parts) > 2 else ""
    )

    return {
        "work_type": work[0] if work else "",
        "work_genitive": work[1] if work else "моєї роботи",
        "deadline": deadline or "",
    }


def site_source_label(site: dict) -> str:
    """Те, що піде в колонку «Джерело»: «Сайт · Магістерська · Тиждень (3–7 днів)»."""

    return " · ".join(
        p for p in (
            SITE_SOURCE,
            site["work_type"],
            site["deadline"],
        ) if p
    )


async def show_site_cta(
    message: types.Message,
    site: dict,
) -> None:

    # Окремий сценарій для сайту:
    # без кнопки «Я з Instagram», з підсумком замовлення
    # і одною кнопкою до менеджера з готовим текстом.

    price_text = (
        f"Я з сайту 🙌 Хочу дізнатись вартість "
        f"{site['work_genitive']}!"
    )

    if site["deadline"]:
        price_text += f" Дедлайн: {site['deadline']}."

    summary = ""

    if site["work_type"]:
        summary += f"📚 Робота: <b>{html.escape(site['work_type'])}</b>\n"
    if site["deadline"]:
        summary += f"⏰ Дедлайн: <b>{html.escape(site['deadline'])}</b>\n"

    text = (
        "Привіт! 👋 Дякуємо, що завітав до нас із сайту EasyFive 🧡\n\n"
        + (f"Твоє замовлення:\n{summary}\n" if summary else "")
        + "Щоб ми швидко порахували вартість, надішли сюди:\n"
        "• тему роботи\n"
        "• кількість сторінок\n"
        "• методичку або вимоги викладача (якщо є)\n\n"
        "Або напиши менеджеру напряму — кнопка нижче 👇🏼\n\n"
        "🎁 −20% на перше замовлення"
    )

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="💬 Написати менеджеру",
                    url=manager_link(price_text),
                )
            ],
        ]
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=kb,
    )


# =========================================================
# ВИЗНАЧЕННЯ ТИПУ ПОВІДОМЛЕННЯ
# =========================================================

def extract_message_content(
    message: types.Message
) -> str:

    if message.text:
        return message.text

    if message.document:
        file_name = (
            message.document.file_name
            or "без назви"
        )

        caption = (
            f"\nПідпис: {message.caption}"
            if message.caption
            else ""
        )

        return (
            f"[Документ: {file_name}]"
            f"{caption}"
        )

    if message.photo:

        caption = (
            f"\nПідпис: {message.caption}"
            if message.caption
            else ""
        )

        return f"[Фото]{caption}"

    if message.video:

        caption = (
            f"\nПідпис: {message.caption}"
            if message.caption
            else ""
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
            if message.caption
            else ""
        )

        return (
            f"[Аудіо: {title}]"
            f"{caption}"
        )

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
            if message.caption
            else ""
        )

        return (
            f"[GIF / анімація]"
            f"{caption}"
        )

    return "[Невідомий тип повідомлення]"


# =========================================================
# /START
# =========================================================

@dp.message(CommandStart())
async def start(
    message: types.Message
) -> None:

    parts = (
        message.text or ""
    ).split(maxsplit=1)

    lead_id = (
        parts[1]
        if len(parts) > 1
        else "unknown"
    )

    user = message.from_user

    # Лід із сайту? -> окреме привітання і зрозуміле джерело
    site = parse_site_lead(lead_id)

    site_info = ""

    if site:
        # У таблицю — лише походження
        source = SITE_SOURCE
        # Менеджеру в групу — подробиці замовлення
        if site["work_type"]:
            site_info += f"📚 Робота: {html.escape(site['work_type'])}\n"
        if site["deadline"]:
            site_info += f"⏰ Дедлайн: {html.escape(site['deadline'])}\n"
        user_leads[user.id] = site_source_label(site)
        await show_site_cta(message, site)
    elif is_instagram_ad(lead_id):
        # У Google Sheets записуємо єдину назву джерела для реклами.
        source = "Бот трафік"
        # Технічний код оголошення зберігаємо для внутрішньої аналітики.
        site_info = f"📊 Кампанія: {html.escape(lead_id)}\n"
        user_leads[user.id] = source
        await show_instagram_ad_cta(message, lead_id)
    else:
        source = lead_id
        user_leads[user.id] = source
        await show_chat_cta(message)

    lead_id = source

    username = (
        f"@{user.username}"
        if user.username
        else "немає username"
    )

    text = (
        f"🆕 <b>Новий лід!</b>\n\n"
        f"👤 Ім'я: {html.escape(user.full_name)}\n"
        f"📱 Username: {html.escape(username)}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Джерело: {html.escape(lead_id)}"
        + (f"\n{site_info.rstrip()}" if site_info else "")
    )

    await bot.send_message(
        GROUP_CHAT_ID,
        text,
        parse_mode="HTML",
    )

    asyncio.create_task(
        send_lead(
            user,
            lead_id,
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
                "full_name":
                    user.full_name,
                "timestamp":
                    int(
                        message.date.timestamp()
                    )
                    if message.date
                    else None,
            })
        )


# =========================================================
# /REPLY ID ТЕКСТ
#
# ВАЖЛИВО:
# ЦЕЙ HANDLER СТОЇТЬ ДО BROADCAST TEXT HANDLER
# =========================================================

@dp.message(
    Command("reply"),
    F.chat.id == GROUP_CHAT_ID,
)
async def reply_by_command(
    message: types.Message
) -> None:

    args = (
        message.text or ""
    ).split(maxsplit=2)

    if len(args) < 3:

        await message.reply(
            "❌ Невірний формат.\n\n"
            "Використовуй:\n"
            "/reply user_id текст"
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
            chat_id=client_id,
            text=f"💬 Менеджер: {text}",
        )

        await message.reply(
            "✅ Відправлено клієнту"
        )

        # Якщо успішно відправили —
        # користувач активний
        asyncio.create_task(
            update_user_status(
                client_id,
                "✅ Активний",
            )
        )

    except TelegramForbiddenError:

        await message.reply(
            "🚫 Не відправлено: "
            "користувач заблокував бота."
        )

        asyncio.create_task(
            update_user_status(
                client_id,
                "🚫 Заблокував бота",
            )
        )

    except TelegramBadRequest as e:

        await message.reply(
            f"⚠️ Telegram не дозволив "
            f"відправити повідомлення:\n{e}"
        )

        asyncio.create_task(
            update_user_status(
                client_id,
                "⚠️ Недоступний",
            )
        )

    except Exception as e:

        print(
            "Reply command error:",
            e
        )

        await message.reply(
            f"❌ Помилка: {e}"
        )


# Фільтри перевіряють стан розсилки ПІД ЧАС кожного повідомлення.
# Це не дозволяє обробнику відповіді клієнту перехопити текст розсилки.
def is_broadcast_draft(message: types.Message) -> bool:
    return bool(
        message.from_user
        and message.from_user.id in broadcast_waiting_for_text
        and message.text
        and not message.text.startswith("/")
    )


def is_not_broadcast_draft(message: types.Message) -> bool:
    return not is_broadcast_draft(message)


# =========================================================
# REPLY НА ПОВІДОМЛЕННЯ КЛІЄНТА В ГРУПІ
# =========================================================

@dp.message(
    F.chat.id == GROUP_CHAT_ID,
    F.reply_to_message,
    is_not_broadcast_draft,
)
async def reply_to_user(
    message: types.Message
) -> None:

    # Команди обробляються окремо
    if (
        message.text
        and message.text.startswith("/")
    ):
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

        await message.reply(
            "⚠️ Не знайшов ID клієнта "
            "в повідомленні, на яке ви відповідаєте."
        )

        return

    client_id = int(
        match.group(1)
    )

    try:

        if message.text:

            await bot.send_message(
                chat_id=client_id,
                text=(
                    f"💬 Менеджер: "
                    f"{message.text}"
                ),
            )

        elif (
            message.document
            or message.photo
            or message.video
            or message.voice
            or message.audio
            or message.animation
            or message.sticker
            or message.video_note
        ):

            await bot.copy_message(
                chat_id=client_id,
                from_chat_id=GROUP_CHAT_ID,
                message_id=message.message_id,
            )

        else:

            await message.reply(
                "⚠️ Цей тип повідомлення "
                "поки не підтримується."
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
            "🚫 Не відправлено: "
            "користувач заблокував бота."
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

        asyncio.create_task(
            update_user_status(
                client_id,
                "⚠️ Недоступний",
            )
        )

    except Exception as e:

        print(
            "Group reply error:",
            e
        )

        await message.reply(
            f"❌ Помилка: {e}"
        )


# =========================================================
# /BROADCAST
# =========================================================

@dp.message(
    Command("broadcast"),
    F.chat.id == GROUP_CHAT_ID,
)
async def broadcast_command(
    message: types.Message
) -> None:

    global broadcast_running

    if broadcast_running:

        await message.reply(
            "⚠️ Розсилка вже виконується."
        )

        return

    user_id = message.from_user.id

    broadcast_waiting_for_text.add(
        user_id
    )

    # Старий незавершений текст видаляємо
    pending_broadcasts.pop(
        user_id,
        None
    )

    await message.reply(
        "📢 <b>Нова розсилка</b>\n\n"
        "Надішли ОДНИМ повідомленням текст, "
        "який потрібно відправити всім клієнтам.\n\n"
        "Для скасування:\n"
        "/cancelbroadcast",
        parse_mode="HTML",
    )


# =========================================================
# /CANCELBROADCAST
# =========================================================

@dp.message(
    Command("cancelbroadcast"),
    F.chat.id == GROUP_CHAT_ID,
)
async def cancel_broadcast(
    message: types.Message
) -> None:

    user_id = message.from_user.id

    broadcast_waiting_for_text.discard(
        user_id
    )

    pending_broadcasts.pop(
        user_id,
        None
    )

    await message.reply(
        "❌ Розсилку скасовано."
    )


# =========================================================
# ТЕКСТ ДЛЯ BROADCAST
#
# ЦЕЙ HANDLER СТОЇТЬ ПІСЛЯ /REPLY ТА /BROADCAST
# =========================================================

@dp.message(
    F.chat.id == GROUP_CHAT_ID,
    F.text,
    is_broadcast_draft,
)
async def capture_broadcast_text(
    message: types.Message
) -> None:

    user_id = message.from_user.id

    # Якщо користувач не запускав /broadcast —
    # взагалі нічого не робимо
    if (
        user_id
        not in broadcast_waiting_for_text
    ):
        return

    # Команди ніколи не використовуємо
    # як текст розсилки
    if message.text.startswith("/"):
        return

    broadcast_waiting_for_text.discard(
        user_id
    )

    pending_broadcasts[
        user_id
    ] = message.text

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✅ Розіслати",
                    callback_data=(
                        f"broadcast_confirm:"
                        f"{user_id}"
                    ),
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="❌ Скасувати",
                    callback_data=(
                        f"broadcast_cancel:"
                        f"{user_id}"
                    ),
                )
            ],
        ]
    )

    await message.reply(
        "📢 <b>Попередній перегляд:</b>\n\n"
        f"{html.escape(message.text)}\n\n"
        "⚠️ Це повідомлення буде "
        "відправлено ВСІМ користувачам "
        "із таблиці.\n\n"
        "Почати розсилку?",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# =========================================================
# BROADCAST — СКАСУВАННЯ КНОПКОЮ
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "broadcast_cancel:"
    )
)
async def broadcast_cancel_callback(
    callback: types.CallbackQuery
) -> None:

    owner_id = int(
        callback.data.split(":")[1]
    )

    if (
        callback.from_user.id
        != owner_id
    ):

        await callback.answer(
            "Це не ваша розсилка.",
            show_alert=True,
        )

        return

    pending_broadcasts.pop(
        owner_id,
        None,
    )

    broadcast_waiting_for_text.discard(
        owner_id
    )

    await callback.message.edit_text(
        "❌ Розсилку скасовано."
    )

    await callback.answer()


# =========================================================
# BROADCAST — ПІДТВЕРДЖЕННЯ
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "broadcast_confirm:"
    )
)
async def broadcast_confirm_callback(
    callback: types.CallbackQuery
) -> None:

    global broadcast_running

    owner_id = int(
        callback.data.split(":")[1]
    )

    if (
        callback.from_user.id
        != owner_id
    ):

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

    broadcast_text = (
        pending_broadcasts.get(
            owner_id
        )
    )

    if not broadcast_text:

        await callback.answer(
            "Текст розсилки не знайдено.",
            show_alert=True,
        )

        return

    broadcast_running = True

    await callback.answer()

    await callback.message.edit_text(
        "⏳ Отримую список клієнтів "
        "із таблиці..."
    )

    try:

        users = (
            await get_broadcast_users()
        )

        if not users:

            await callback.message.edit_text(
                "❌ Не вдалося отримати "
                "користувачів із Google Sheets."
            )

            return

        total = len(users)

        await callback.message.edit_text(
            "🚀 <b>Розсилка почалась</b>\n\n"
            f"👥 Користувачів у базі: {total}",
            parse_mode="HTML",
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

                client_id = int(
                    telegram_id
                )

            except (
                ValueError,
                TypeError,
            ):

                errors += 1
                continue

            try:

                await bot.send_message(
                    chat_id=client_id,
                    text=broadcast_text,
                )

                sent += 1

                await update_user_status(
                    telegram_id,
                    "✅ Активний",
                )

            except TelegramForbiddenError:

                blocked += 1

                await update_user_status(
                    telegram_id,
                    "🚫 Заблокував бота",
                )

            except TelegramRetryAfter as e:

                # Telegram просить зачекати
                await asyncio.sleep(
                    e.retry_after + 1
                )

                try:

                    await bot.send_message(
                        chat_id=client_id,
                        text=broadcast_text,
                    )

                    sent += 1

                    await update_user_status(
                        telegram_id,
                        "✅ Активний",
                    )

                except TelegramForbiddenError:

                    blocked += 1

                    await update_user_status(
                        telegram_id,
                        "🚫 Заблокував бота",
                    )

                except TelegramBadRequest:

                    errors += 1

                    await update_user_status(
                        telegram_id,
                        "⚠️ Недоступний",
                    )

                except Exception as e2:

                    print(
                        "Broadcast retry error "
                        f"{telegram_id}:",
                        e2,
                    )

                    errors += 1

                    await update_user_status(
                        telegram_id,
                        "⚠️ Помилка",
                    )

            except TelegramBadRequest:

                errors += 1

                await update_user_status(
                    telegram_id,
                    "⚠️ Недоступний",
                )

            except Exception as e:

                print(
                    "Broadcast error "
                    f"{telegram_id}:",
                    e,
                )

                errors += 1

                await update_user_status(
                    telegram_id,
                    "⚠️ Помилка",
                )

            # Маленька пауза між повідомленнями
            await asyncio.sleep(0.08)

        pending_broadcasts.pop(
            owner_id,
            None,
        )

        await bot.send_message(
            GROUP_CHAT_ID,
            "✅ <b>Розсилку завершено!</b>\n\n"
            f"👥 Всього в базі: {total}\n"
            f"✅ Відправлено: {sent}\n"
            f"🚫 Заблокували бота: {blocked}\n"
            f"⚠️ Інші помилки: {errors}",
            parse_mode="HTML",
        )

    except Exception as e:

        print(
            "Broadcast fatal error:",
            e,
        )

        await bot.send_message(
            GROUP_CHAT_ID,
            f"❌ Помилка розсилки:\n{e}",
        )

    finally:

        broadcast_running = False


# =========================================================
# ПОВІДОМЛЕННЯ ВІД КЛІЄНТА -> ГРУПА
# =========================================================

@dp.message(
    F.chat.type == "private"
)
async def forward_to_group(
    message: types.Message
) -> None:

    # Команди сюди не перекидаємо
    if (
        message.text
        and message.text.startswith("/")
    ):
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
        "unknown",
    )

    text = (
        f"💬 <b>Повідомлення від клієнта</b>\n"
        f"👤 {html.escape(user.full_name)} ({html.escape(username)})\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Lead ID: "
        f"<code>{html.escape(lead_id)}</code>\n\n"
        f"➡️ {html.escape(content)}"
    )

    await bot.send_message(
        GROUP_CHAT_ID,
        text,
        parse_mode="HTML",
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
# /RESTART
# =========================================================

@dp.message(Command("restart"))
async def restart_cmd(
    message: types.Message
) -> None:

    await show_chat_cta(
        message
    )


# =========================================================
# /CONTACTS
# =========================================================

@dp.message(Command("contacts"))
async def contacts_cmd(
    message: types.Message
) -> None:

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✉️ Написати менеджеру",
                    url=(
                        f"https://t.me/"
                        f"{MANAGER_USERNAME}"
                    ),
                )
            ]
        ]
    )

    await message.answer(
        "📞 <b>Контакти Easy.Five</b>\n\n"
        "Менеджер відповість "
        "найближчим часом 👇",
        parse_mode="HTML",
        reply_markup=kb,
    )


# =========================================================
# ЗАПУСК
# =========================================================

async def main() -> None:

    print("🤖 Bot started")

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(main())
