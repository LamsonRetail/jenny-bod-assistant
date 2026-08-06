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


def _authorized_ids() -> set[str]:
    """Người được cấp quyền kích hoạt meeting notes (config meeting_authorized_ids)."""
    val = db.all_configs().get("meeting_authorized_ids", {})
    ids = val.get("ids", []) if isinstance(val, dict) else val
    return {str(i) for i in ids}


def _is_authorized_meeting(creator: dict, attendees: list[dict]) -> bool:
    auth = _authorized_ids()
    if not auth:  # chưa cấu hình → không chặn (giữ hành vi cũ)
        return True
    return creator.get("open_id") in auth \
        or any(a.get("open_id") in auth for a in attendees)


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


def _parse_attendees(event_id: str) -> tuple[list[dict], dict | None, str]:
    """→ (attendees không gồm Jenny, creator, rsvp_status của Jenny)."""
    raw = lark_user.event_attendees(event_id)
    me_id = lark_user.me().get("open_id")
    attendees, creator, my_rsvp = [], None, ""
    for a in raw:
        oid = a.get("user_id") or a.get("open_id") or ""
        if not oid:
            continue
        if oid == me_id:
            my_rsvp = a.get("rsvp_status", "")
            continue
        person = {"open_id": oid, "name": a.get("display_name") or ""}
        attendees.append(person)
        if a.get("is_organizer"):
            creator = person
    return attendees, creator or (attendees[0] if attendees else None), my_rsvp


_rsvp_handled: set[str] = set()


def _rsvp_upcoming(now: int) -> None:
    """Lời mời họp mới: người có quyền → accept, không → decline + nhắn lý do.

    Instance họp định kỳ có thể không reply được qua API (Lark hạn chế) —
    khi đó vẫn nhắn người mời; xử lý notes không phụ thuộc RSVP.
    """
    try:
        events = lark_user.list_events(now - 3600, now + 14 * 86400)
    except Exception:
        return
    for e in events:
        event_id = e.get("event_id", "")
        if e.get("status") == "cancelled" or event_id in _rsvp_handled:
            continue
        try:
            attendees, creator, my_rsvp = _parse_attendees(event_id)
        except Exception:
            continue
        if my_rsvp != "needs_action" or not creator:
            _rsvp_handled.add(event_id)
            continue
        ok = _is_authorized_meeting(creator, attendees)
        replied = True
        try:
            lark_user.reply_event(event_id, "accept" if ok else "decline")
        except Exception as ex:
            replied = False
            log.warning("Không RSVP được '%s' (instance họp định kỳ?): %s",
                        e.get("summary"), str(ex)[:150])
        log.info("Họp '%s' (tạo bởi %s): %s%s", e.get("summary"), creator["name"],
                 "CÓ QUYỀN" if ok else "KHÔNG CÓ QUYỀN",
                 " · RSVP OK" if replied else " · RSVP không gửi được")
        if not ok:
            try:
                message_user(creator["open_id"],
                             f"Em xin phép từ chối lời mời họp “{e.get('summary')}” — "
                             "anh/chị chưa nằm trong danh sách được cấp quyền sử dụng "
                             "Jenny cho meeting notes. Cần cấp quyền thì liên hệ BOD ạ.")
            except Exception:
                log.exception("Không nhắn được người mời")
        _rsvp_handled.add(event_id)


def _watch() -> None:
    now = int(time.time())
    _rsvp_upcoming(now)
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

        if not _is_authorized_meeting(creator, attendees):
            db.sb().table("meetings").insert({
                "event_id": event_id, "title": e.get("summary"),
                "creator_open_id": creator["open_id"], "creator_name": creator["name"],
                "status": "skipped", "attendees": attendees}).execute()
            log.info("Bỏ qua họp '%s' — không có người được cấp quyền tham dự",
                     e.get("summary"))
            continue

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


def match_or_create_for_creator(sender_open_id: str) -> dict | None:
    """Như trên, nhưng nếu chưa có hồ sơ họp thì tra lịch: người gửi là organizer
    của một sự kiện đã bắt đầu (kể cả lịch chưa tới giờ kết thúc — họp xong sớm)
    → tạo hồ sơ ngay để xử lý bản ghi không phải chờ."""
    import time as _t

    m = match_pending_for_creator(sender_open_id)
    if m:
        return m
    now = int(_t.time())
    try:
        events = lark_user.list_events(now - LOOKBACK_HOURS * 3600, now + 3600)
    except Exception:
        return None
    events.sort(key=lambda x: int(x.get("start_time", {}).get("timestamp") or 0),
                reverse=True)
    me_id = lark_user.me().get("open_id")
    for e in events:
        if e.get("status") == "cancelled":
            continue
        start_ts = int(e.get("start_time", {}).get("timestamp") or 0)
        if not start_ts or start_ts > now:
            continue  # chưa bắt đầu
        event_id = e.get("event_id", "")
        if db.sb().table("meetings").select("id").eq("event_id", event_id).execute().data:
            continue  # đã có hồ sơ (kể cả skipped)
        try:
            raw = lark_user.event_attendees(event_id)
        except Exception:
            continue
        attendees, creator = [], None
        for a in raw:
            oid = a.get("user_id") or a.get("open_id") or ""
            if not oid or oid == me_id:
                continue
            person = {"open_id": oid, "name": a.get("display_name") or ""}
            attendees.append(person)
            if a.get("is_organizer"):
                creator = person
        creator = creator or (attendees[0] if attendees else None)
        if not creator or creator["open_id"] != sender_open_id:
            continue
        end_ts = int(e.get("end_time", {}).get("timestamp") or now)
        row = {
            "event_id": event_id, "title": e.get("summary") or "(không tiêu đề)",
            "start_at": dt.datetime.fromtimestamp(start_ts, VN).isoformat(),
            "end_at": dt.datetime.fromtimestamp(min(end_ts, now), VN).isoformat(),
            "creator_open_id": creator["open_id"], "creator_name": creator["name"],
            "attendees": attendees,
        }
        inserted = db.sb().table("meetings").insert(row).execute()
        log.info("Tạo hồ sơ họp từ bản ghi gửi sớm: %s", row["title"])
        return inserted.data[0] if inserted.data else None
    return None


def match_or_create_by_topic(topic: str, vc_meeting: dict) -> dict | None:
    """Khớp hồ sơ họp theo tiêu đề (từ event VC); chưa có thì tạo từ lịch/event."""
    res = (db.sb().table("meetings").select("*")
           .in_("status", ["awaiting_content", "draft"])
           .order("end_at", desc=True).limit(20).execute()).data
    for m in res:
        if (m.get("title") or "").strip().lower() == topic.strip().lower():
            return m

    # chưa có: tra lịch Jenny tìm sự kiện cùng tên trong 24h gần đây
    import time as _t
    now = int(_t.time())
    try:
        events = lark_user.list_events(now - 24 * 3600, now + 3600)
    except Exception:
        events = []
    for e in events:
        if (e.get("summary") or "").strip().lower() != topic.strip().lower():
            continue
        event_id = e.get("event_id", "")
        exist = db.sb().table("meetings").select("*").eq("event_id", event_id).execute()
        if exist.data:
            return exist.data[0]
        try:
            attendees, creator, _ = _parse_attendees(event_id)
        except Exception:
            attendees, creator = [], None
        if not _is_authorized_meeting(creator or {}, attendees):
            log.info("Bản ghi '%s': không thuộc người được cấp quyền → bỏ qua", topic)
            return None
        creator = creator or (attendees[0] if attendees else None)
        if not creator:
            return None
        end_ts = int(e.get("end_time", {}).get("timestamp") or now)
        row = db.sb().table("meetings").insert({
            "event_id": event_id, "title": topic,
            "end_at": dt.datetime.fromtimestamp(min(end_ts, now), VN).isoformat(),
            "creator_open_id": creator["open_id"], "creator_name": creator["name"],
            "attendees": attendees,
        }).execute().data
        return row[0] if row else None

    # Không thấy trên lịch của Jenny → cuộc họp KHÔNG mời Jenny → không xử lý.
    # (Nguyên tắc: chỉ transcript cuộc họp có add Jenny tham gia.)
    log.info("Bản ghi '%s': không có trên lịch Jenny (không được mời) → bỏ qua", topic)
    return None


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
        transcript = None
        if minutes_url and audio is None:
            if "/minutes/" in minutes_url:
                audio, mime = lark_user.download_minutes_media(minutes_url)
            else:  # URL bản ghi từ event VC
                audio, mime = lark_user.download_url(minutes_url)
        if transcript is None:
            transcript = transcribe.transcribe_audio(audio, mime or "audio/mp4",
                                                     title=title)

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
