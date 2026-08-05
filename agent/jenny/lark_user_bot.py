"""Lark gateway chạy bằng TÀI KHOẢN người dùng — polling, không cần bot.

Vòng lặp: liệt kê chat account Jenny tham gia → đọc tin nhắn mới → xử lý
(mention/trigger trong group, mọi tin trong p2p) → trả lời với tư cách account.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from . import agent, config, db, lark_user

log = logging.getLogger(__name__)

POLL_INTERVAL = 5          # giây giữa các vòng đọc tin
CHAT_REFRESH = 120         # giây giữa các lần làm mới danh sách chat


def _admin_ids() -> set[str]:
    val = db.all_configs().get("lark_admin_ids", {})
    ids = val.get("ids", []) if isinstance(val, dict) else val
    return {str(i) for i in ids}


def _trigger_names() -> list[str]:
    rules = db.all_configs().get("reply_rules", {})
    return [str(t).lower() for t in rules.get("trigger_names", ["jenny"])]


def _parse_text(msg: dict) -> str:
    if msg.get("msg_type") != "text":
        return ""
    try:
        text = json.loads(msg["body"]["content"]).get("text", "")
    except Exception:
        return ""
    for m in (msg.get("mentions") or []):
        text = text.replace(m.get("key", ""), f"@{m.get('name', '')}")
    return text.strip()


def _mentioned_me(msg: dict, my_open_id: str, text: str) -> bool:
    for m in (msg.get("mentions") or []):
        if m.get("id") == my_open_id or m.get("id", {}) == {"open_id": my_open_id}:
            return True
        if isinstance(m.get("id"), dict) and m["id"].get("open_id") == my_open_id:
            return True
    low = text.lower()
    return any(t in low for t in _trigger_names())


URL_RE = None


def _index_resources(chat: dict, msg: dict, text: str, sender_id: str) -> None:
    """Tự index link/file được share vào danh bạ tài nguyên (bảng resources).

    Chạy cho MỌI tin nhắn Jenny thấy (kể cả không được tag) — âm thầm, không trả lời.
    """
    import re
    global URL_RE
    if URL_RE is None:
        URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

    rows = []
    chat_id, chat_name = chat["chat_id"], chat.get("name") or "(chat riêng)"

    if msg.get("msg_type") == "file":
        try:
            body = json.loads(msg["body"]["content"])
            rows.append({"kind": "file", "file_token": body.get("file_key", ""),
                         "url": f"lark-file:{body.get('file_key','')}",
                         "title": body.get("file_name", "file"),
                         "context_note": ""})
        except Exception:
            pass

    for url in URL_RE.findall(text or ""):
        row = {"kind": "link", "url": url, "title": "", "excerpt": "",
               "context_note": text[:300]}
        if re.search(r"\.(larksuite|feishu)\.(com|cn)/(wiki|docx|docs|base|sheets)/", url):
            row["kind"] = "lark_doc"
            try:  # đọc nhanh tiêu đề + trích đoạn bằng quyền account Jenny
                content = lark_user.read_document(url)
                row["title"] = (content.splitlines() or [""])[0].strip("# ")[:200]
                row["excerpt"] = content[:800]
            except Exception as e:
                row["excerpt"] = f"(chưa đọc được: {e})"
        rows.append(row)

    for row in rows:
        row.update({"channel": "lark", "chat_id": chat_id, "chat_name": chat_name,
                    "sender_id": sender_id})
        db.save_resource(row)
        log.info("Đã index tài nguyên: %s (%s)", row.get("title") or row.get("url"), row["kind"])


def _maybe_meeting_recording(msg: dict, text: str, sender_id: str) -> bool:
    """Bản ghi họp (file audio hoặc link Minutes) từ người tạo họp đang chờ →
    chạy pipeline gỡ băng trong thread riêng. Trả về True nếu đã nhận xử lý."""
    import re
    import threading

    from . import meetings, transcribe

    audio_job = None
    if msg.get("msg_type") == "file":
        try:
            body = json.loads(msg["body"]["content"])
            mime = transcribe.mime_for(body.get("file_name", ""))
            if mime:
                audio_job = ("file", msg.get("message_id", ""), body.get("file_key", ""), mime)
        except Exception:
            return False
    else:
        m = re.search(r"https?://[^\s]+/minutes/[A-Za-z0-9]+", text or "")
        if m:
            audio_job = ("minutes", m.group(0), "", "")
    if not audio_job:
        return False

    meeting = meetings.match_or_create_for_creator(sender_id)
    if not meeting:
        if audio_job[0] == "minutes":  # người gửi không phải creator cuộc họp nào
            lark_user.send_text(
                msg.get("chat_id", ""),
                "Em nhận được link bản ghi nhưng chưa khớp với cuộc họp nào của "
                "anh/chị trên lịch của em (em cần được mời trong calendar event, và "
                "người gửi bản ghi là người tạo họp). Anh/chị kiểm tra giúp em nhé.")
            return True
        return False

    def _run() -> None:
        try:
            if audio_job[0] == "file":
                data, ct = lark_user.download_message_resource(audio_job[1], audio_job[2])
                meetings.process_recording(meeting, audio=data, mime=audio_job[3] or ct)
            else:
                meetings.process_recording(meeting, minutes_url=audio_job[1])
        except Exception:
            log.exception("Pipeline bản ghi lỗi")

    threading.Thread(target=_run, daemon=True).start()
    return True


async def _handle_message(chat: dict, msg: dict, my_open_id: str) -> None:
    text = _parse_text(msg)
    sender0 = (msg.get("sender", {}).get("id") or "")
    try:
        _index_resources(chat, msg, text, sender0)
    except Exception:
        log.exception("index resource lỗi")
    try:
        if _maybe_meeting_recording(msg, text, sender0):
            return
    except Exception:
        log.exception("meeting recording check lỗi")
    if not text:
        return
    chat_id = chat["chat_id"]
    is_group = chat.get("chat_mode", "group") == "group" and chat.get("chat_type") != "p2p"
    sender_id = (msg.get("sender", {}).get("id") or "")

    conv = db.get_or_create_conversation("lark", chat_id, chat.get("name"), is_group)
    db.log_message(conv["id"], "in", text, sender_id=sender_id)

    low = text.lower().strip()
    if low.startswith("/id"):
        lark_user.send_text(chat_id, f"Chat ID: {chat_id}\nUser open_id: {sender_id}")
        return
    if "/approve" in low:
        if sender_id in _admin_ids():
            db.set_whitelisted(conv["id"], True)
            lark_user.send_text(chat_id, "✅ Chat này đã được duyệt. Em sẵn sàng phục vụ!")
        else:
            lark_user.send_text(chat_id, "Anh/chị không có quyền duyệt chat này.")
        return

    if is_group and not _mentioned_me(msg, my_open_id, text):
        return
    if not conv["whitelisted"]:
        lark_user.send_text(chat_id,
                            "Chat này chưa được duyệt sử dụng Jenny.\n"
                            f"Chat ID: {chat_id} — admin gõ /approve tại đây để duyệt.")
        return

    # "Typing indicator": gửi placeholder ngay, xong thì edit thành câu trả lời
    placeholder_id = ""
    try:
        placeholder_id = lark_user.send_text(chat_id, "⏳ Em đang xử lý, chờ em chút nhé…")[0]
    except Exception:
        log.warning("Không gửi được placeholder")

    # Hồ sơ người hỏi (từ danh bạ tổ chức + ghi chú tự học)
    sender_name, sender_context = sender_id or "Người dùng", ""
    try:
        from . import org
        p = org.get_person(sender_id) if sender_id else None
        if p:
            sender_name = p["name"]
            sender_context = f"{p['name']} — {p.get('job_title') or 'chưa rõ chức danh'}"
            if p.get("department_path"):
                sender_context += f", {p['department_path']}"
            notes = (p.get("learned_notes") or "").strip()
            if notes:
                sender_context += f". Ghi chú: {notes[-600:]}"
            sender_context += f" (open_id: {sender_id})"
    except Exception:
        log.exception("Tra hồ sơ người hỏi lỗi")
    sender_context = (sender_context + f" · chat_id hiện tại: {chat_id}").strip(" ·")

    history = db.recent_messages(conv["id"], config.MAX_HISTORY_MESSAGES)[:-1]
    system_prompt = agent.build_system_prompt(chat.get("name"), is_group, channel="Lark")
    prompt = agent.build_prompt(history, sender_name, text, sender_context=sender_context)
    try:
        reply = await agent.run(prompt, system_prompt, conversation_id=conv["id"])
    except Exception:
        log.exception("agent.run failed")
        err = "Em gặp lỗi khi xử lý, anh/chị thử lại giúp em nhé."
        try:
            lark_user.update_text(placeholder_id, err)
        except Exception:
            lark_user.send_text(chat_id, err)
        return

    usage = reply.usage or {}
    db.log_message(conv["id"], "out", reply.text, session_id=reply.session_id,
                   tokens_input=usage.get("input_tokens"),
                   tokens_output=usage.get("output_tokens"))
    first, rest = reply.text[:9000], reply.text[9000:]
    try:
        lark_user.update_text(placeholder_id, first)
    except Exception:
        lark_user.send_text(chat_id, first)
    if rest:
        lark_user.send_text(chat_id, rest)


def _p2p_chats() -> list[dict]:
    """Chat riêng không nằm trong API list_chats — bootstrap từ config.

    `lark_p2p_partners.ids`: open_id những người được chat riêng với Jenny.
    Người mới → Jenny nhắn chào 1 lần để mở chat, lưu chat_id vào `lark_p2p_map`.
    """
    cfgs = db.all_configs()
    partners = cfgs.get("lark_p2p_partners", {}).get("ids", [])
    mapping: dict = dict(cfgs.get("lark_p2p_map", {}))
    changed = False
    for oid in partners:
        if oid in mapping:
            continue
        try:
            chat_id = lark_user.send_text_to_user(
                oid, "Em là Jenny — trợ lý BOD. Chat riêng đã kết nối, "
                     "anh/chị nhắn em bất cứ lúc nào ạ!")
            mapping[oid] = chat_id
            conv = db.get_or_create_conversation("lark", chat_id, None, False)
            # nằm trong danh sách p2p partners = đã được cấp quyền chat riêng
            db.set_whitelisted(conv["id"], True)
            changed = True
            log.info("Mở p2p chat với %s → %s", oid, chat_id)
        except Exception as e:
            log.warning("Không mở được p2p với %s: %s", oid, e)
    if changed:
        db.sb().table("configs").upsert({
            "key": "lark_p2p_map", "value": mapping,
            "description": "Map open_id → chat_id p2p (Jenny tự quản lý)",
        }, on_conflict="key").execute()
    return [{"chat_id": cid, "name": None, "chat_mode": "p2p", "chat_type": "p2p"}
            for cid in mapping.values()]


def run_bot() -> None:
    config.require("LARK_APP_ID", "LARK_APP_SECRET")

    # Chờ đến khi có OAuth token (đăng nhập qua /lark/oauth/start/<secret>)
    while True:
        try:
            lark_user.access_token()
            break
        except Exception as e:
            log.warning("Chưa có Lark user token: %s — thử lại sau 30s", e)
            time.sleep(30)

    my = lark_user.me()
    log.info("Jenny Lark (user account): %s (%s)", my.get("name"), my.get("open_id"))

    chats: list[dict] = []
    cursors: dict[str, int] = {}
    last_chat_refresh = 0.0

    while True:
        try:
            now_sec = int(time.time())
            if time.time() - last_chat_refresh > CHAT_REFRESH or not chats:
                chats = lark_user.list_chats() + _p2p_chats()
                last_chat_refresh = time.time()
                log.info("Đang theo dõi %d chat (gồm %d chat riêng)",
                         len(chats), sum(1 for c in chats if c.get("chat_type") == "p2p"))

            for chat in chats:
                cid = chat["chat_id"]
                since = cursors.get(cid, now_sec)  # chat mới thấy: bỏ qua lịch sử
                try:
                    msgs = lark_user.list_messages(cid, since)
                except Exception as e:
                    log.warning("Đọc chat %s lỗi: %s", cid, e)
                    continue
                latest = since
                for m in msgs:
                    create_sec = int(int(m.get("create_time", "0")) / 1000)
                    latest = max(latest, create_sec + 1)
                    sender_id = m.get("sender", {}).get("id", "")
                    if sender_id == my.get("open_id"):
                        continue  # tin của chính Jenny
                    try:
                        asyncio.run(_handle_message(chat, m, my.get("open_id", "")))
                    except Exception:
                        log.exception("handle_message lỗi")
                cursors[cid] = latest
        except Exception:
            log.exception("Vòng poll lỗi — tiếp tục")
        time.sleep(POLL_INTERVAL)
