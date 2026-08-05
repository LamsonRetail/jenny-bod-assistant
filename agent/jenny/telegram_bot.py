"""Telegram gateway — long polling, không cần domain/webhook."""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import agent, config, db

log = logging.getLogger(__name__)


def _admin_ids() -> set[str]:
    val = db.all_configs().get("telegram_admin_ids", [])
    ids = val.get("ids", []) if isinstance(val, dict) else val
    return {str(i) for i in ids}


def _split_message(text: str) -> list[str]:
    chunks = []
    while text:
        chunks.append(text[: config.TELEGRAM_MAX_LEN])
        text = text[config.TELEGRAM_MAX_LEN :]
    return chunks


async def _is_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Trong group chỉ trả lời khi được tag @bot, reply tin của bot, hoặc gọi tên."""
    msg = update.effective_message
    bot_username = (context.bot.username or "").lower()
    text = (msg.text or "").lower()
    if bot_username and f"@{bot_username}" in text:
        return True
    if msg.reply_to_message and msg.reply_to_message.from_user \
            and msg.reply_to_message.from_user.id == context.bot.id:
        return True
    triggers = db.all_configs().get("reply_rules", {}).get("trigger_names", ["jenny"])
    return any(str(t).lower() in text for t in triggers)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    await update.effective_message.reply_text(
        f"Chat ID: `{chat.id}`\nUser ID: `{user.id}`", parse_mode="Markdown")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/approve — admin (id nằm trong config telegram_admin_ids) duyệt chat hiện tại."""
    chat = update.effective_chat
    user = update.effective_user
    if str(user.id) not in _admin_ids():
        await update.effective_message.reply_text("Anh/chị không có quyền duyệt chat này.")
        return
    conv = db.get_or_create_conversation(
        "telegram", str(chat.id), chat.title or chat.full_name, chat.type != "private")
    db.set_whitelisted(conv["id"], True)
    await update.effective_message.reply_text("✅ Chat này đã được duyệt. Em sẵn sàng phục vụ!")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not msg.text or not chat:
        return

    is_group = chat.type != "private"
    conv = db.get_or_create_conversation(
        "telegram", str(chat.id), chat.title or chat.full_name, is_group)
    sender = user.full_name if user else "unknown"
    db.log_message(conv["id"], "in", msg.text,
                   sender_id=str(user.id) if user else None, sender_name=sender)

    # Group: chỉ phản hồi khi được gọi
    if is_group and not await _is_mentioned(update, context):
        return

    if not conv["whitelisted"]:
        await msg.reply_text(
            "Chat này chưa được duyệt sử dụng Jenny.\n"
            f"Chat ID: `{chat.id}` — gửi ID này cho quản trị viên, "
            "hoặc admin gõ /approve tại đây.", parse_mode="Markdown")
        return

    history = db.recent_messages(conv["id"], config.MAX_HISTORY_MESSAGES)[:-1]
    system_prompt = agent.build_system_prompt(chat.title, is_group)
    prompt = agent.build_prompt(history, sender, msg.text)

    async def _keep_typing() -> None:
        # Telegram tắt "typing…" sau ~5s — gửi lại liên tục tới khi có kết quả
        while True:
            try:
                await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(_keep_typing())
    try:
        reply = await agent.run(prompt, system_prompt, conversation_id=conv["id"])
    except Exception:
        log.exception("agent.run failed")
        await msg.reply_text("Em gặp lỗi khi xử lý, anh/chị thử lại giúp em nhé.")
        return
    finally:
        typing_task.cancel()

    usage = reply.usage or {}
    db.log_message(conv["id"], "out", reply.text, session_id=reply.session_id,
                   tokens_input=usage.get("input_tokens"),
                   tokens_output=usage.get("output_tokens"))
    for chunk in _split_message(reply.text):
        await msg.reply_text(chunk)


def run_bot() -> None:
    config.require("TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler(["id", "start"], cmd_id))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Jenny Telegram bot: bắt đầu long polling…")
    app.run_polling(allowed_updates=["message"])
