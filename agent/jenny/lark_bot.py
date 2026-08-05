"""Lark gateway — WebSocket long connection, không cần webhook công khai.

Chạy thành service riêng (jenny-lark) song song với gateway Telegram (jenny),
dùng chung agent core + Supabase.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
)

from . import agent, config, db

log = logging.getLogger(__name__)

_client: lark.Client | None = None
_bot_open_id: str | None = None
_bot_names: set[str] = set()

LARK_TEXT_MAX = 10000  # an toàn dưới giới hạn của Lark


def _api() -> lark.Client:
    global _client
    if _client is None:
        _client = (lark.Client.builder()
                   .app_id(config.LARK_APP_ID)
                   .app_secret(config.LARK_APP_SECRET)
                   .domain(config.LARK_DOMAIN)
                   .build())
    return _client


def _fetch_bot_info() -> None:
    """Lấy open_id + tên bot để nhận diện khi được @mention trong group."""
    global _bot_open_id, _bot_names
    req = (lark.BaseRequest.builder()
           .http_method(lark.HttpMethod.GET)
           .uri("/open-apis/bot/v3/info")
           .token_types({lark.AccessTokenType.TENANT})
           .build())
    resp = _api().request(req)
    try:
        bot = json.loads(resp.raw.content).get("bot", {})
        _bot_open_id = bot.get("open_id")
        name = (bot.get("app_name") or "").strip()
        _bot_names = {n for n in (name.lower(), "jenny") if n}
        log.info("Bot Lark: %s (open_id=%s)", name, _bot_open_id)
    except Exception:
        log.exception("Không đọc được bot info — mention theo tên 'jenny'")
        _bot_names = {"jenny"}


def _send_text(chat_id: str, text: str) -> None:
    for i in range(0, len(text), LARK_TEXT_MAX):
        chunk = text[i : i + LARK_TEXT_MAX]
        req = (CreateMessageRequest.builder()
               .receive_id_type("chat_id")
               .request_body(CreateMessageRequestBody.builder()
                             .receive_id(chat_id)
                             .msg_type("text")
                             .content(json.dumps({"text": chunk}, ensure_ascii=False))
                             .build())
               .build())
        resp = _api().im.v1.message.create(req)
        if not resp.success():
            log.error("Gửi Lark lỗi %s: %s", resp.code, resp.msg)


def _admin_ids() -> set[str]:
    val = db.all_configs().get("lark_admin_ids", {})
    ids = val.get("ids", []) if isinstance(val, dict) else val
    return {str(i) for i in ids}


async def _handle(data: P2ImMessageReceiveV1) -> None:
    msg = data.event.message
    if msg.message_type != "text":
        return
    text = (json.loads(msg.content).get("text") or "").strip()
    if not text:
        return

    mentions = msg.mentions or []
    mentioned = any(m.id and m.id.open_id == _bot_open_id for m in mentions)
    for m in mentions:  # thay placeholder @_user_1 bằng tên thật cho dễ đọc
        text = text.replace(m.key or "", f"@{m.name or ''}").strip()

    chat_id = msg.chat_id
    is_group = msg.chat_type == "group"
    sender = data.event.sender.sender_id
    sender_id = sender.open_id if sender else None

    conv = db.get_or_create_conversation("lark", chat_id, None, is_group)
    db.log_message(conv["id"], "in", text, sender_id=sender_id)

    cmd = text.replace(f"@{next(iter(_bot_names), '')}", "").strip().lower()
    if cmd == "/id":
        _send_text(chat_id, f"Chat ID: {chat_id}\nUser open_id: {sender_id}")
        return
    if cmd == "/approve":
        if sender_id in _admin_ids():
            db.set_whitelisted(conv["id"], True)
            _send_text(chat_id, "✅ Chat này đã được duyệt. Em sẵn sàng phục vụ!")
        else:
            _send_text(chat_id, "Anh/chị không có quyền duyệt chat này.")
        return

    if is_group and not mentioned \
            and not any(n in text.lower() for n in _bot_names):
        return

    if not conv["whitelisted"]:
        _send_text(chat_id,
                   "Chat này chưa được duyệt sử dụng Jenny.\n"
                   f"Chat ID: {chat_id} — gửi ID này cho quản trị viên, "
                   "hoặc admin gõ /approve tại đây.")
        return

    history = db.recent_messages(conv["id"], config.MAX_HISTORY_MESSAGES)[:-1]
    system_prompt = agent.build_system_prompt(None, is_group, channel="Lark")
    prompt = agent.build_prompt(history, sender_id or "Người dùng", text)

    try:
        reply = await agent.run(prompt, system_prompt, conversation_id=conv["id"])
    except Exception:
        log.exception("agent.run failed")
        _send_text(chat_id, "Em gặp lỗi khi xử lý, anh/chị thử lại giúp em nhé.")
        return

    usage = reply.usage or {}
    db.log_message(conv["id"], "out", reply.text, session_id=reply.session_id,
                   tokens_input=usage.get("input_tokens"),
                   tokens_output=usage.get("output_tokens"))
    _send_text(chat_id, reply.text)


def _on_message(data: P2ImMessageReceiveV1) -> None:
    # Callback của SDK là sync — xử lý trong thread riêng để không nghẽn WS.
    threading.Thread(target=lambda: asyncio.run(_handle(data)), daemon=True).start()


def run_bot() -> None:
    config.require("LARK_APP_ID", "LARK_APP_SECRET")
    _fetch_bot_info()
    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(_on_message)
               .build())
    ws = lark.ws.Client(config.LARK_APP_ID, config.LARK_APP_SECRET,
                        event_handler=handler,
                        domain=config.LARK_DOMAIN,
                        log_level=lark.LogLevel.INFO)
    log.info("Jenny Lark bot: bắt đầu WebSocket long connection…")
    ws.start()
