"""Lark gateway chạy bằng TÀI KHOẢN người dùng — polling, không cần bot.

Vòng lặp: liệt kê chat account Jenny tham gia → đọc tin nhắn mới → xử lý
(mention/trigger trong group, mọi tin trong p2p) → trả lời với tư cách account.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from . import agent, answer_cache, config, db, lark_user, policy

log = logging.getLogger(__name__)

POLL_INTERVAL = 5          # giây giữa các vòng đọc tin
CHAT_REFRESH = 120         # giây giữa các lần làm mới danh sách chat
THREAD_LOOKBACK = 7200     # quét phát hiện thread trong 2h gần đây


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


_last_placeholder: dict[str, float] = {}


def _placeholder_cfg() -> dict:
    """config `placeholder` — tin '⏳ đang xử lý'."""
    cfg = db.all_configs().get("placeholder", {}) or {}
    return {
        "enabled": cfg.get("enabled", True),
        # Hỏi liên tục trong khoảng này thì KHÔNG gửi lại tin chờ (đỡ rác chat)
        "cooldown_sec": int(cfg.get("cooldown_sec", 180)),
        "text": cfg.get("text", "⏳ Em đang xử lý, chờ em chút nhé…"),
    }


def _should_send_placeholder(key: str, cfg: dict) -> bool:
    if not cfg["enabled"]:
        return False
    now = time.time()
    last = _last_placeholder.get(key, 0.0)
    if now - last < cfg["cooldown_sec"]:
        return False
    _last_placeholder[key] = now
    if len(_last_placeholder) > 500:          # giới hạn bộ nhớ
        for k in sorted(_last_placeholder, key=_last_placeholder.get)[:200]:
            _last_placeholder.pop(k, None)
    return True


def _voice_cfg() -> dict:
    """Cấu hình xử lý tin nhắn thoại (config `voice_note`)."""
    cfg = db.all_configs().get("voice_note", {}) or {}
    return {
        "enabled": cfg.get("enabled", True),
        "max_duration_sec": int(cfg.get("max_duration_sec", 300)),
        "group_requires_reply": cfg.get("group_requires_reply", True),
        "echo_transcript": cfg.get("echo_transcript", True),
    }


def _recall(message_id: str) -> None:
    """Thu hồi tin chờ. Thất bại thì bỏ qua — không được chặn việc trả lời."""
    if not message_id:
        return
    try:
        lark_user.recall_message(message_id)
    except Exception as e:
        log.warning("Không thu hồi được tin chờ %s: %s", message_id, e)


def _voice_transcript(chat: dict, msg: dict, in_thread: bool) -> str:
    """Tin nhắn thoại Lark → văn bản. Trả '' nếu không thuộc diện xử lý.

    Lọc để không gỡ băng tràn lan (tốn tiền): chat riêng luôn xử lý; trong group
    chỉ xử lý khi tin nằm trong thread Jenny theo dõi hoặc là reply của tin khác.
    """
    from . import transcribe

    cfg = _voice_cfg()
    if not cfg["enabled"]:
        return ""
    is_group = chat.get("chat_mode", "group") == "group" and chat.get("chat_type") != "p2p"
    if is_group and cfg["group_requires_reply"] and not (in_thread or msg.get("parent_id")):
        return ""

    try:
        body = json.loads(msg["body"]["content"])
    except Exception:
        return ""
    file_key = body.get("file_key", "")
    if not file_key:
        return ""
    dur_sec = int(body.get("duration") or 0) / 1000
    if dur_sec > cfg["max_duration_sec"]:
        log.info("Bỏ qua tin thoại dài %.0fs (ngưỡng %ds)", dur_sec, cfg["max_duration_sec"])
        return ""

    data, _ = lark_user.download_message_resource(msg["message_id"], file_key)
    text = transcribe.transcribe_audio(transcribe.to_wav(data), "audio/wav",
                                       title="voice-note")
    log.info("Tin thoại %.0fs → %d ký tự", dur_sec, len(text))
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


async def _handle_message(chat: dict, msg: dict, my_open_id: str,
                          in_thread: bool = False) -> None:
    text = _parse_text(msg)
    sender0 = (msg.get("sender", {}).get("id") or "")
    # Trong thread: trả lời vào đúng thread (reply theo message_id); ngoài thread: gửi chat.
    reply_to = msg.get("message_id", "") if in_thread else ""
    is_group = chat.get("chat_mode", "group") == "group" and chat.get("chat_type") != "p2p"

    def _say(t: str, mention: bool = False) -> str:
        # Trong group: tag người hỏi để họ nhận thông báo. Chat riêng thì không cần.
        ats = [sender0] if (mention and is_group and sender0) else None
        if reply_to:
            return lark_user.reply_in_thread(reply_to, t, mention_open_ids=ats)
        ids = lark_user.send_text(chat["chat_id"], t, mention_open_ids=ats)
        return ids[0] if ids else ""
    try:
        _index_resources(chat, msg, text, sender0)
    except Exception:
        log.exception("index resource lỗi")
    try:
        if _maybe_meeting_recording(msg, text, sender0):
            return
    except Exception:
        log.exception("meeting recording check lỗi")

    # Tin nhắn thoại → gỡ băng rồi xử lý như tin chữ
    is_voice = False
    if not text and msg.get("msg_type") == "audio":
        try:
            text = _voice_transcript(chat, msg, in_thread)
            is_voice = bool(text)
        except Exception as e:
            log.exception("Gỡ băng tin thoại lỗi")
            _say(f"Em nhận được tin thoại nhưng chưa gỡ băng được ạ — {str(e)[:250]}\n\n"
                 "Trong lúc chờ, anh/chị nhắn lại bằng chữ giúp em nhé.")
            return

    if not text:
        return
    chat_id = chat["chat_id"]
    sender_id = sender0

    conv = db.get_or_create_conversation("lark", chat_id, chat.get("name"), is_group)
    db.log_message(conv["id"], "in", ("🎤 " if is_voice else "") + text, sender_id=sender_id)

    low = text.lower().strip()
    if low.startswith("/id"):
        _say(f"Chat ID: {chat_id}\nUser open_id: {sender_id}")
        return
    if "/approve" in low:
        if sender_id in _admin_ids():
            db.set_whitelisted(conv["id"], True)
            _say("✅ Chat này đã được duyệt. Em sẵn sàng phục vụ!")
        else:
            _say("Anh/chị không có quyền duyệt chat này.")
        return

    # Tin thoại trong thread Jenny đang theo dõi: coi như đang nói với Jenny
    # (người nói không "tag" được ai trong tin thoại).
    if is_group and not (is_voice and in_thread) \
            and not _mentioned_me(msg, my_open_id, text):
        return
    # Mở cho toàn công ty (config open_access); tắt config → quay lại luồng /approve.
    if not conv["whitelisted"] and not policy.is_open_access():
        _say("Chat này chưa được duyệt sử dụng Jenny.\n"
             f"Chat ID: {chat_id} — admin gõ /approve tại đây để duyệt.")
        return

    can_assign = policy.is_assignment_admin("lark", sender_id)

    # RÀO 1 — chủ đề hạn chế: từ chối TRƯỚC khi gọi LLM (BOD/CEO/GDKD bỏ qua).
    _hit = policy.restricted_topic(text, is_admin=can_assign)
    if _hit:
        log.info("chặn chủ đề '%s' (khớp '%s') — người gửi %s", _hit[0], _hit[1], sender_id)
        _msg = policy.refusal_message(_hit[0])
        db.log_message(conv["id"], "out", _msg)
        _say(_msg)
        return

    # RÀO 2 — nhất quán: câu tương tự đã trả lời thì dùng lại NGUYÊN VĂN.
    tier = "admin" if can_assign else "user"
    _cached = answer_cache.lookup(text, tier)
    if _cached:
        db.log_message(conv["id"], "out", _cached["answer"])
        _say(_cached["answer"])
        return

    # "Typing indicator": gửi tin chờ, xong thì THU HỒI rồi gửi câu trả lời mới
    # (phải gửi tin mới chứ không sửa tin cũ, vì tin đã sửa không tag được người hỏi).
    # Hỏi liên tục thì bỏ tin chờ để không rác chat.
    placeholder_id = ""
    ph_cfg = _placeholder_cfg()
    if _should_send_placeholder(f"{chat_id}:{sender_id}", ph_cfg):
        try:
            placeholder_id = _say(ph_cfg["text"])
        except Exception:
            log.warning("Không gửi được tin chờ")

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
    try:
        bod = db.all_configs().get("bod_members", {}).get("members", [])
        me_bod = next((b for b in bod if b.get("open_id") == sender_id), None)
        if me_bod:
            sender_context += (f". ⭐ THÀNH VIÊN BOD — phụ trách: {me_bod.get('domains')}. "
                               "Người này được giao việc cho nhân sự (assignment_create).")
        from . import assignments
        my_jobs = assignments.list_active(sender_id)
        if my_jobs:
            brief = "; ".join(f"[{a['id'][:8]}…] {a['title']} ({a['status']})"
                              for a in my_jobs[:5])
            sender_context += (f". Việc BOD đang giao cho người này: {brief} — nếu tin nhắn "
                               "là nộp kết quả, đối chiếu expected_outcome (assignment_list) "
                               "và xử lý theo skill bod-delegation.")
    except Exception:
        log.exception("Tra assignments/BOD lỗi")
    sender_context = (sender_context + f" · chat_id hiện tại: {chat_id}").strip(" ·")
    if reply_to:
        sender_context += (f" · Tin này ở trong 1 THREAD; thread_reply_to: {reply_to} "
                           "(truyền vào schedule_create nếu người dùng muốn cập nhật định "
                           "kỳ vào đúng thread này).")

    history = db.recent_messages(conv["id"], config.MAX_HISTORY_MESSAGES)[:-1]
    system_prompt = agent.build_system_prompt(chat.get("name"), is_group, channel="Lark",
                                              can_assign=can_assign,
                                              restricted_on=not can_assign)
    prompt = agent.build_prompt(history, sender_name, text, sender_context=sender_context)
    try:
        reply = await agent.run(
            prompt, system_prompt, conversation_id=conv["id"],
            allowed_tools=policy.allowed_tools(agent.ALLOWED_TOOLS, can_assign=can_assign))
    except Exception:
        log.exception("agent.run failed")
        _recall(placeholder_id)
        _say("Em gặp lỗi khi xử lý, anh/chị thử lại giúp em nhé.", mention=True)
        return

    usage = reply.usage or {}
    db.log_message(conv["id"], "out", reply.text, session_id=reply.session_id,
                   tokens_input=usage.get("input_tokens"),
                   tokens_output=usage.get("output_tokens"))
    answer_cache.store(text, tier, reply.text)
    # Tin thoại: chép lại nội dung nghe được để người nói phát hiện ngay nếu nghe sai
    display = reply.text
    if is_voice and _voice_cfg()["echo_transcript"]:
        display = f"🎤 Em nghe: «{text[:300]}»\n\n{reply.text}"
    first, rest = display[:9000], display[9000:]
    _recall(placeholder_id)          # xoá tin chờ trước khi trả lời
    _say(first, mention=True)
    if rest:
        _say(rest)


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
    threads: dict[str, dict] = {}   # thread_id → chat dict
    tcursors: dict[str, int] = {}
    handled: set[str] = set()       # message_id đã xử lý — chống lặp tuyệt đối
    handled_order: list[str] = []
    last_chat_refresh = 0.0
    my_id = my.get("open_id", "")

    # Thread đã biết được LƯU BỀN qua config → restart vẫn nhớ, không mất dấu
    # (trước đây restart xoá sạch threads, tag trong thread cũ không được trả lời).
    known_threads: dict[str, str] = dict(
        db.all_configs().get("lark_known_threads", {}) or {})
    persisted_loaded = False

    def _persist_thread(tid: str, cid: str) -> None:
        if not tid or known_threads.get(tid) == cid:
            return
        known_threads[tid] = cid
        try:
            db.sb().table("configs").upsert({
                "key": "lark_known_threads", "value": known_threads,
                "description": "Thread Jenny đang theo dõi (bền qua restart)",
            }, on_conflict="key").execute()
        except Exception:
            log.warning("Không lưu được lark_known_threads")

    def _seen(mid: str) -> bool:
        """True nếu message_id đã xử lý (và đánh dấu nếu chưa)."""
        if not mid or mid in handled:
            return True
        handled.add(mid)
        handled_order.append(mid)
        if len(handled_order) > 3000:  # giới hạn bộ nhớ
            handled.discard(handled_order.pop(0))
        return False

    while True:
        try:
            now_sec = int(time.time())
            if time.time() - last_chat_refresh > CHAT_REFRESH or not chats:
                chats = lark_user.list_chats() + _p2p_chats()
                last_chat_refresh = time.time()
                chat_by_id = {c["chat_id"]: c for c in chats}
                # Nạp thread đã biết (lưu bền) — chỉ đọc reply MỚI sau restart, tránh
                # trả lời lại tin cũ; nhưng vẫn giữ theo dõi các thread cũ.
                if not persisted_loaded:
                    for tid, cid in known_threads.items():
                        if tid not in threads and cid in chat_by_id:
                            threads[tid] = chat_by_id[cid]
                            tcursors[tid] = now_sec
                    persisted_loaded = True
                # Phát hiện thread: quét tin 2h gần đây ở mỗi group (thread root/tin
                # có thread_id) — vì reply tag trong thread không hiện ở container chat.
                for chat in chats:
                    if chat.get("chat_type") == "p2p":
                        continue
                    try:
                        recent = lark_user.list_messages(chat["chat_id"],
                                                         now_sec - THREAD_LOOKBACK)
                    except Exception:
                        continue
                    for m in recent:
                        tid = m.get("thread_id")
                        if tid and tid not in threads:
                            threads[tid] = chat
                            tcursors[tid] = now_sec - THREAD_LOOKBACK  # đọc reply gần đây
                            _persist_thread(tid, chat["chat_id"])
                log.info("Đang theo dõi %d chat (gồm %d chat riêng), %d thread",
                         len(chats), sum(1 for c in chats if c.get("chat_type") == "p2p"),
                         len(threads))

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
                    # Phát hiện thread để theo dõi reply theo luồng
                    tid = m.get("thread_id")
                    if tid and tid not in threads:
                        threads[tid] = chat
                        tcursors[tid] = create_sec  # đọc reply MỚI kể từ khi phát hiện
                        _persist_thread(tid, chat["chat_id"])
                    if m.get("sender", {}).get("id", "") == my_id:
                        continue
                    if _seen(m.get("message_id", "")):
                        continue
                    try:
                        asyncio.run(_handle_message(chat, m, my_id))
                    except Exception:
                        log.exception("handle_message lỗi")
                cursors[cid] = max(latest, now_sec)  # luôn tiến cursor, tránh đọc lại

            # Poll reply trong các thread đã biết (tin tag trong thread chỉ hiện ở đây)
            for tid, chat in list(threads.items()):
                since = tcursors.get(tid, now_sec)
                try:
                    tmsgs = lark_user.list_thread_messages(tid, since)
                except Exception as e:
                    log.debug("Đọc thread %s lỗi: %s", tid, e)
                    continue
                latest = since
                for m in tmsgs:
                    create_sec = int(int(m.get("create_time", "0")) / 1000)
                    latest = max(latest, create_sec + 1)
                    if m.get("sender", {}).get("id", "") == my_id:
                        continue
                    if _seen(m.get("message_id", "")):
                        continue
                    try:
                        asyncio.run(_handle_message(chat, m, my_id, in_thread=True))
                    except Exception:
                        log.exception("handle_message (thread) lỗi")
                tcursors[tid] = max(latest, now_sec)  # luôn tiến cursor, tránh đọc lại
        except Exception:
            log.exception("Vòng poll lỗi — tiếp tục")
        time.sleep(POLL_INTERVAL)
