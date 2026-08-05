"""Meeting notes tự động.

Luồng: scheduler quét lịch Jenny (5 phút/lần) → sự kiện vừa kết thúc mà Jenny
được mời → tạo hồ sơ họp + nhắn người tạo xin nội dung → agent soạn notes
(tools meeting_*) → người tạo duyệt → phát hành: lưu kho, gửi mọi người tham dự,
tạo task, ghi số liệu.
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from . import db, lark_user

log = logging.getLogger(__name__)

VN = dt.timezone(dt.timedelta(hours=7))
WATCH_INTERVAL = 300          # giây giữa các lần quét lịch
LOOKBACK_HOURS = 12           # chỉ xử lý họp kết thúc trong N giờ gần đây
MIN_ATTENDEES = 2             # bỏ qua sự kiện 1 người (blocker cá nhân...)

_last_watch = 0.0


def _register_p2p(open_id: str, chat_id: str) -> None:
    """Đăng ký p2p chat vào map để poller theo dõi + whitelist (nhân sự nội bộ)."""
    cfgs = db.all_configs()
    mapping = dict(cfgs.get("lark_p2p_map", {}))
    if mapping.get(open_id) == chat_id:
        return
    mapping[open_id] = chat_id
    db.sb().table("configs").upsert({
        "key": "lark_p2p_map", "value": mapping,
        "description": "Map open_id → chat_id p2p (Jenny tự quản lý)",
    }, on_conflict="key").execute()
    conv = db.get_or_create_conversation("lark", chat_id, None, False)
    db.set_whitelisted(conv["id"], True)


def message_user(open_id: str, text: str) -> None:
    mapping = db.all_configs().get("lark_p2p_map", {})
    chat_id = mapping.get(open_id, "")
    if chat_id:
        lark_user.send_text(chat_id, text)  # đã có kênh → gửi chunked
        return
    chat_id = lark_user.send_text_to_user(open_id, text[:9000])
    if chat_id:
        _register_p2p(open_id, chat_id)
        if len(text) > 9000:
            lark_user.send_text(chat_id, text[9000:])


def maybe_watch() -> None:
    global _last_watch
    if time.time() - _last_watch < WATCH_INTERVAL:
        return
    _last_watch = time.time()
    try:
        _watch()
    except Exception:
        log.exception("Quét lịch họp lỗi")


def _watch() -> None:
    now = int(time.time())
    events = lark_user.list_events(now - LOOKBACK_HOURS * 3600, now + 60)
    for e in events:
        if e.get("status") == "cancelled":
            continue
        end_ts = int(e.get("end_time", {}).get("timestamp") or 0)
        if not end_ts or end_ts > now:
            continue  # chưa kết thúc
        event_id = e.get("event_id", "")
        exists = db.sb().table("meetings").select("id").eq("event_id", event_id).execute()
        if exists.data:
            continue

        try:
            raw = lark_user.event_attendees(event_id)
        except Exception as ex:
            log.warning("Không đọc được attendees %s: %s", event_id, ex)
            raw = []
        me_id = lark_user.me().get("open_id")
        attendees, creator = [], None
        for a in raw:
            if a.get("type") not in ("user", None):
                continue
            oid = a.get("user_id") or a.get("open_id") or ""
            if not oid or oid == me_id:
                continue
            person = {"open_id": oid, "name": a.get("display_name") or ""}
            attendees.append(person)
            if a.get("is_organizer"):
                creator = person
        if len(attendees) < MIN_ATTENDEES:
            db.sb().table("meetings").insert({
                "event_id": event_id, "title": e.get("summary"),
                "status": "skipped", "attendees": attendees}).execute()
            continue
        creator = creator or attendees[0]

        title = e.get("summary") or "(không tiêu đề)"
        start_ts = int(e.get("start_time", {}).get("timestamp") or 0)
        db.sb().table("meetings").insert({
            "event_id": event_id, "title": title,
            "start_at": dt.datetime.fromtimestamp(start_ts, VN).isoformat() if start_ts else None,
            "end_at": dt.datetime.fromtimestamp(end_ts, VN).isoformat(),
            "creator_open_id": creator["open_id"], "creator_name": creator["name"],
            "attendees": attendees,
        }).execute()
        log.info("Họp mới kết thúc: %s (%d người, tạo bởi %s)", title,
                 len(attendees), creator["name"])
        try:
            message_user(creator["open_id"],
                         f"Chào anh/chị, cuộc họp “{title}” vừa kết thúc. Em sẽ tự gỡ băng "
                         "và soạn meeting notes — anh/chị gửi em MỘT trong các thứ sau:\n"
                         "1) Link Lark Minutes (nếu họp có bật Recording)\n"
                         "2) File ghi âm cuộc họp (m4a/mp3/wav/mp4)\n"
                         "3) Hoặc dán các ý chính nếu không có bản ghi\n"
                         "Notes soạn xong em gửi anh/chị duyệt trước khi phát hành ạ.")
        except Exception:
            log.exception("Không nhắn được người tạo họp")


# ---------- pipeline gỡ băng → draft notes ----------

def match_pending_for_creator(sender_open_id: str) -> dict | None:
    """Cuộc họp gần nhất đang chờ nội dung mà người gửi là người tạo."""
    res = (db.sb().table("meetings").select("*")
           .eq("creator_open_id", sender_open_id)
           .in_("status", ["awaiting_content", "draft"])
           .order("end_at", desc=True).limit(1).execute())
    return res.data[0] if res.data else None


def process_recording(meeting: dict, audio: bytes | None = None,
                      mime: str = "", minutes_url: str = "") -> None:
    """Gỡ băng (Gemini) → soạn notes (Claude) → gửi owner duyệt. Chạy trong thread riêng."""
    import asyncio

    from . import transcribe
    creator = meeting["creator_open_id"]
    title = meeting.get("title") or "(không tiêu đề)"
    try:
        message_user(creator, f"⏳ Em nhận được bản ghi của “{title}”. Đang gỡ băng "
                              "(Việt-Anh) và soạn notes — khoảng vài phút ạ.")
        if minutes_url and audio is None:
            audio, mime = lark_user.download_minutes_media(minutes_url)
        transcript = transcribe.transcribe_audio(audio, mime or "audio/mp4", title=title)

        from . import agent
        attendees = ", ".join(a.get("name", "?") for a in (meeting.get("attendees") or []))
        sp = agent.build_system_prompt(title, True, channel="Lark")
        prompt = (
            f"Soạn meeting notes hoàn chỉnh (markdown) cho cuộc họp “{title}” "
            f"(kết thúc {str(meeting.get('end_at'))[:16]}, tham dự: {attendees}) "
            "từ bản gỡ băng dưới đây.\n\n"
            "Format bắt buộc:\n# <tiêu đề> · <ngày>\n- Tham dự: ...\n"
            "## Nội dung chính (gạch đầu dòng theo chủ đề)\n## Quyết định\n"
            "## Action items (mỗi dòng: việc — người phụ trách — deadline nếu có)\n\n"
            "Viết tiếng Việt, giữ thuật ngữ tiếng Anh gốc, số liệu chính xác theo transcript, "
            "KHÔNG bịa thêm. Chỉ trả về markdown notes, không giải thích.\n"
            "LƯU Ý: transcript từ máy có thể ghi sai thuật ngữ tiếng Anh/tên riêng "
            "(vd 'tịch tập shop'=TikTok Shop, 'Latman'=last month, 'ơ dớn'=Urgent, "
            "'Bacto School'=Back to School) — hãy hiệu đính các lỗi kiểu này theo ngữ cảnh "
            "kinh doanh của công ty; chỗ nào không chắc thì ghi kèm (?).\n\n"
            f"=== BẢN GỠ BĂNG ===\n{transcript[:150000]}")
        reply = asyncio.run(agent.run(prompt, sp))
        notes = reply.text.strip()
        if not notes:
            raise RuntimeError("Soạn notes rỗng")

        save_draft(meeting["id"], notes)
        message_user(creator,
                     f"📝 DRAFT MEETING NOTES — “{title}”\n\n{notes}\n\n"
                     "―――\nAnh/chị xem giúp em: reply **OK** để em phát hành "
                     "(gửi mọi người tham dự + tạo task cho action items), "
                     "hoặc góp ý để em chỉnh sửa ạ.")
        log.info("Đã gửi draft notes '%s' cho owner", title)
    except Exception as e:
        log.exception("Pipeline gỡ băng lỗi: %s", title)
        try:
            message_user(creator,
                         f"⚠️ Em gặp lỗi khi xử lý bản ghi của “{title}”: {str(e)[:200]}\n"
                         "Anh/chị có thể gửi lại file khác, hoặc dán các ý chính để em "
                         "soạn notes thủ công ạ.")
        except Exception:
            pass


# ---------- helpers cho tools ----------

def pending() -> list[dict]:
    res = (db.sb().table("meetings").select("*")
           .in_("status", ["awaiting_content", "draft"])
           .order("end_at", desc=True).limit(10).execute())
    return res.data


def save_draft(meeting_id: str, notes_md: str) -> None:
    db.sb().table("meetings").update({
        "notes_md": notes_md, "status": "draft",
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }).eq("id", meeting_id).execute()


def finalize(meeting_id: str, tasks_created: int) -> dict:
    res = db.sb().table("meetings").select("*").eq("id", meeting_id).execute()
    if not res.data:
        raise RuntimeError("Không thấy meeting này")
    m = res.data[0]
    if not (m.get("notes_md") or "").strip():
        raise RuntimeError("Meeting chưa có notes_md (dùng meeting_save_draft trước)")

    from . import lark_memory
    day = (m.get("end_at") or lark_memory.today_vn())[:10]
    fname = f"{day}-{lark_memory.slugify(m.get('title') or 'hop')}.md"
    up = lark_memory.save_markdown("meetings", fname, m["notes_md"])
    lark_memory.append_index(
        f"- [meetings/{fname}] (token:{up.get('file_token','')}) · {day} · "
        f"{m.get('title')} (notes đã duyệt)")

    sent = 0
    for a in m.get("attendees") or []:
        try:
            message_user(a["open_id"],
                         f"📝 Meeting notes: {m.get('title')} ({day}) — đã được duyệt\n\n"
                         + m["notes_md"])
            sent += 1
        except Exception:
            log.warning("Không gửi được notes cho %s", a.get("name"))

    db.sb().table("meetings").update({
        "status": "distributed", "file_token": up.get("file_token", ""),
        "tasks_created": tasks_created,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }).eq("id", meeting_id).execute()
    return {"file": fname, "sent_to": sent, "tasks_created": tasks_created}
