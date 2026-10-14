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
        f"👤 {user.full_name} ({username})\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📣 Lead ID: "
        f"<code>{lead_id}</code>\n\n"
        f"➡️ {content}"
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
