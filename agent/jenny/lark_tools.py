"""Lark MCP server (in-process) — tools thao tác Lark bằng account Jenny.

Nhóm: đọc tài liệu · lịch họp · task · gửi tin nhắn · kho bộ nhớ.
Giờ hiển thị/nhập đều là giờ Việt Nam (UTC+7).
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import db

log = logging.getLogger(__name__)

MAX_CHARS = 30000
VN = dt.timezone(dt.timedelta(hours=7))


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def _err(e: Exception) -> dict:
    return _text(f"Lỗi: {e}")


def _parse_vn(s: str) -> dt.datetime:
    """'2026-08-05 14:00' hoặc '2026-08-05T14:00' → datetime giờ VN."""
    return dt.datetime.fromisoformat(s.strip().replace(" ", "T")).replace(tzinfo=VN)


def _fmt_ts(ts: str | int) -> str:
    return dt.datetime.fromtimestamp(int(ts), VN).strftime("%Y-%m-%d %H:%M")


# ---------- Đọc tài liệu ----------

@tool("read_lark_document",
      "Đọc nội dung tài liệu Lark bằng quyền của account Jenny (KHÔNG dùng WebFetch với "
      "link Lark — sẽ bị chặn đăng nhập). Nhận link wiki/docx/base.",
      {"url": str})
async def read_lark_document(args: dict) -> dict:
    url = (args.get("url") or "").strip()
    if not url:
        return _text("Thiếu url.")
    try:
        from . import lark_user
        content = lark_user.read_document(url)
    except Exception as e:
        return _err(e)
    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS] + f"\n\n[... đã cắt, tài liệu dài {len(content):,} ký tự]"
    return _text(content or "(tài liệu trống)")


# ---------- Lịch họp ----------

@tool("calendar_list_events",
      "Xem lịch của Jenny (và các lịch được share vào account Jenny) trong khoảng ngày. "
      "Ngày dạng YYYY-MM-DD, giờ VN.",
      {"from_date": str, "to_date": str})
async def calendar_list_events(args: dict) -> dict:
    try:
        from . import lark_user
        start = int(_parse_vn(args["from_date"] + " 00:00").timestamp())
        end = int(_parse_vn(args["to_date"] + " 23:59").timestamp())
        events = lark_user.list_events(start, end)
        if not events:
            return _text("Không có sự kiện nào trong khoảng này.")
        lines = []
        for e in events:
            if e.get("status") == "cancelled":
                continue
            st = e.get("start_time", {}).get("timestamp")
            et = e.get("end_time", {}).get("timestamp")
            lines.append(f"- [{e.get('event_id')}] {e.get('summary') or '(không tiêu đề)'} · "
                         f"{_fmt_ts(st)} → {_fmt_ts(et)}")
        return _text("\n".join(lines) or "Không có sự kiện.")
    except Exception as e:
        return _err(e)


@tool("calendar_create_event",
      "Tạo sự kiện/cuộc họp trên lịch Jenny. start/end dạng 'YYYY-MM-DD HH:MM' giờ VN. "
      "LUÔN xác nhận với người dùng trước khi tạo.",
      {"summary": str, "start": str, "end": str, "description": str})
async def calendar_create_event(args: dict) -> dict:
    try:
        from . import lark_user
        ev = lark_user.create_event(
            args["summary"],
            int(_parse_vn(args["start"]).timestamp()),
            int(_parse_vn(args["end"]).timestamp()),
            args.get("description", ""))
        return _text(f"Đã tạo sự kiện [{ev.get('event_id')}] {args['summary']} · "
                     f"{args['start']} → {args['end']}")
    except Exception as e:
        return _err(e)


@tool("calendar_delete_event",
      "Hủy 1 sự kiện trên lịch Jenny theo event_id (lấy từ calendar_list_events). "
      "LUÔN xác nhận với người dùng trước khi hủy.",
      {"event_id": str})
async def calendar_delete_event(args: dict) -> dict:
    try:
        from . import lark_user
        lark_user.delete_event(args["event_id"])
        return _text("Đã hủy sự kiện.")
    except Exception as e:
        return _err(e)


# ---------- Task ----------

@tool("task_create",
      "Tạo task trên Lark Task. due dạng 'YYYY-MM-DD HH:MM' (giờ VN, bỏ trống nếu không có "
      "deadline). assignee_open_id: open_id người được giao (bỏ trống = Jenny tự theo dõi). "
      "LUÔN xác nhận với người dùng trước khi tạo.",
      {"summary": str, "due": str, "assignee_open_id": str, "description": str})
async def task_create(args: dict) -> dict:
    try:
        from . import lark_user
        due_ms = None
        if (args.get("due") or "").strip():
            due_ms = int(_parse_vn(args["due"]).timestamp() * 1000)
        t = lark_user.create_task(args["summary"], due_ms,
                                  args.get("description", ""),
                                  args.get("assignee_open_id", ""))
        return _text(f"Đã tạo task [{t.get('guid')}] {args['summary']}"
                     + (f" · hạn {args['due']}" if due_ms else ""))
    except Exception as e:
        return _err(e)


@tool("task_list",
      "Liệt kê task mà Jenny tham gia/theo dõi (kèm trạng thái, deadline).", {})
async def task_list(args: dict) -> dict:
    try:
        from . import lark_user
        tasks = lark_user.list_tasks()
        if not tasks:
            return _text("Chưa có task nào.")
        lines = []
        for t in tasks[:50]:
            due = t.get("due", {}).get("timestamp")
            done = "✅" if t.get("completed_at") and t["completed_at"] != "0" else "⬜"
            lines.append(f"{done} [{t.get('guid')}] {t.get('summary')}"
                         + (f" · hạn {_fmt_ts(int(due) // 1000)}" if due else ""))
        return _text("\n".join(lines))
    except Exception as e:
        return _err(e)


@tool("task_complete",
      "Đánh dấu hoàn thành 1 task theo guid (lấy từ task_list).", {"task_guid": str})
async def task_complete(args: dict) -> dict:
    try:
        from . import lark_user
        lark_user.complete_task(args["task_guid"])
        return _text("Đã đánh dấu hoàn thành.")
    except Exception as e:
        return _err(e)


# ---------- Gửi tin nhắn ----------

@tool("send_lark_message",
      "Gửi tin nhắn đến 1 chat Lark khác (group/người) theo chat_id. CHỈ gửi được vào chat "
      "đã whitelist. Dùng khi cần chuyển tiếp/báo cáo sang chat khác — trong hội thoại "
      "hiện tại thì trả lời bình thường, không dùng tool này.",
      {"chat_id": str, "text": str})
async def send_lark_message(args: dict) -> dict:
    try:
        from . import lark_user
        chat_id = args["chat_id"]
        res = (db.sb().table("conversations").select("whitelisted")
               .eq("channel", "lark").eq("chat_id", chat_id).execute())
        if not res.data or not res.data[0]["whitelisted"]:
            return _text("Từ chối: chat này chưa được whitelist.")
        lark_user.send_text(chat_id, args["text"])
        return _text("Đã gửi.")
    except Exception as e:
        return _err(e)


# ---------- Meeting notes ----------

@tool("meeting_list_pending",
      "Danh sách các cuộc họp đang chờ xử lý notes (chờ nội dung từ người tạo, hoặc "
      "đã có draft chờ duyệt). Dùng khi người tạo họp gửi nội dung/duyệt notes để biết "
      "đang nói về meeting nào.", {})
async def meeting_list_pending(args: dict) -> dict:
    try:
        from . import meetings
        rows = meetings.pending()
        if not rows:
            return _text("Không có cuộc họp nào chờ xử lý.")
        lines = []
        for m in rows:
            lines.append(f"- meeting_id: {m['id']} · “{m.get('title')}” · kết thúc "
                         f"{str(m.get('end_at'))[:16]} · người tạo: {m.get('creator_name')} "
                         f"({m.get('creator_open_id')}) · trạng thái: {m['status']}"
                         + (f"\n  DRAFT hiện tại:\n{m['notes_md'][:1500]}" if m.get("notes_md") else ""))
        return _text("\n".join(lines))
    except Exception as e:
        return _err(e)


@tool("meeting_save_draft",
      "Lưu bản draft meeting notes (markdown) cho 1 cuộc họp. Sau khi lưu, gửi draft "
      "cho người tạo họp trong chat và đề nghị họ duyệt (reply OK) hoặc góp ý sửa.",
      {"meeting_id": str, "notes_md": str})
async def meeting_save_draft(args: dict) -> dict:
    try:
        from . import meetings
        meetings.save_draft(args["meeting_id"], args["notes_md"])
        return _text("Đã lưu draft. Giờ gửi nội dung draft cho người tạo họp duyệt.")
    except Exception as e:
        return _err(e)


@tool("meeting_finalize",
      "Phát hành meeting notes SAU KHI người tạo họp đã xác nhận duyệt: lưu kho, gửi "
      "notes cho toàn bộ người tham dự, ghi nhận số task đã tạo. tasks_created: số task "
      "bạn đã tạo bằng task_create cho các action item (tạo task TRƯỚC khi gọi tool này).",
      {"meeting_id": str, "tasks_created": int})
async def meeting_finalize(args: dict) -> dict:
    try:
        from . import meetings
        res = meetings.finalize(args["meeting_id"], int(args.get("tasks_created") or 0))
        return _text(f"Đã phát hành: lưu {res['file']}, gửi cho {res['sent_to']} người, "
                     f"{res['tasks_created']} task.")
    except Exception as e:
        return _err(e)


# ---------- Cơ cấu tổ chức ----------

@tool("org_lookup",
      "Tra cứu danh bạ tổ chức LSR: tìm người theo tên/chức danh/phòng ban "
      "(vd 'cung ứng', 'kế toán trưởng', 'Thi'). Trả về tên, chức danh, phòng ban, "
      "ghi chú đã học về người đó. Dùng khi cần biết ai phụ trách gì, ai là ai.",
      {"keyword": str})
async def org_lookup(args: dict) -> dict:
    try:
        from . import org
        rows = org.search_people(args.get("keyword", ""))
        if not rows:
            return _text("Không tìm thấy ai khớp. (Danh bạ sync từ Lark Contacts hằng ngày)")
        lines = []
        for p in rows:
            line = f"- {p['name']} — {p.get('job_title') or '?'} · {p.get('department_path') or '?'}"
            if (p.get("learned_notes") or "").strip():
                line += f"\n  Ghi chú: {p['learned_notes'][-300:]}"
            line += f"\n  open_id: {p['open_id']}"
            lines.append(line)
        return _text("\n".join(lines))
    except Exception as e:
        return _err(e)


@tool("person_note_save",
      "Lưu 1 ghi chú CÔNG VIỆC ngắn về một người (dự án đang làm, mảng phụ trách, mối "
      "quan tâm nghiệp vụ) để lần sau trả lời họ đúng ngữ cảnh hơn. KHÔNG lưu thông tin "
      "đời tư hay đánh giá cá nhân. note: 1 câu ngắn gọn.",
      {"open_id": str, "note": str})
async def person_note_save(args: dict) -> dict:
    try:
        from . import org
        org.add_note(args["open_id"], args["note"])
        return _text("Đã ghi chú.")
    except Exception as e:
        return _err(e)


# ---------- Danh bạ tài nguyên (link/file được share trong chat) ----------

def _fmt_resources(rows: list[dict]) -> str:
    if not rows:
        return "Không tìm thấy tài nguyên nào."
    lines = []
    for r in rows:
        t = r.get("title") or r.get("url") or "?"
        lines.append(f"- [{r.get('kind')}] {t}\n  url: {r.get('url')} · chat: {r.get('chat_name')} "
                     f"· {str(r.get('created_at'))[:10]}"
                     + (f"\n  ngữ cảnh: {r['context_note'][:150]}" if r.get("context_note") else ""))
    return "\n".join(lines)


@tool("search_resources",
      "Tìm trong danh bạ link/file/doc đã được share trong các chat Lark (Jenny tự index). "
      "Dùng khi được hỏi về tài liệu/link liên quan chủ đề nào đó. Tìm theo từ khóa "
      "(khớp tiêu đề, url, trích đoạn, câu chat đi kèm). Muốn đọc sâu 1 kết quả lark_doc: "
      "dùng read_lark_document với url của nó.",
      {"keyword": str})
async def search_resources(args: dict) -> dict:
    try:
        rows = db.search_resources(args.get("keyword", ""))
        return _text(_fmt_resources(rows))
    except Exception as e:
        return _err(e)


@tool("watch_document",
      "Thêm 1 tài liệu Lark (docs/base/wiki) vào danh sách Jenny theo dõi comment — "
      "sau đó ai tag Jenny trong comment của tài liệu đó sẽ được trả lời tự động. "
      "Dùng khi người dùng gửi link và nhờ 'theo dõi/để ý tài liệu này'.",
      {"url": str, "title": str})
async def watch_document(args: dict) -> dict:
    try:
        from . import doc_watch
        doc_watch.watch_doc_url(args["url"], args.get("title", ""))
        return _text("Đã thêm vào danh sách theo dõi comment (quét mỗi 2 phút).")
    except Exception as e:
        return _err(e)


@tool("recent_resources",
      "Liệt kê các link/file/doc được share gần đây nhất trong các chat Lark.", {})
async def recent_resources(args: dict) -> dict:
    try:
        return _text(_fmt_resources(db.recent_resources()))
    except Exception as e:
        return _err(e)


# ---------- Việc BOD giao (assignments) ----------

@tool("assignment_create",
      "Tạo việc BOD giao cho 1 nhân sự — CHỈ khi người giao là thành viên BOD. Bắt buộc "
      "đủ 4 yếu tố (thiếu thì hỏi lại trước): title (việc gì), context (bối cảnh), "
      "expected_outcome (đầu ra cụ thể để đánh giá), pic_open_id (tra org_lookup). "
      "deadline 'YYYY-MM-DD HH:MM' giờ VN (bỏ trống nếu không có). assigner_open_id = "
      "open_id người giao (có trong thông tin người nhắn). Tự động: tạo Lark task + "
      "nhắn PIC đầy đủ thông tin.",
      {"title": str, "context": str, "expected_outcome": str, "deadline": str,
       "pic_open_id": str, "assigner_open_id": str})
async def assignment_create(args: dict) -> dict:
    try:
        from . import assignments
        row = assignments.create(args["title"], args.get("context", ""),
                                 args.get("expected_outcome", ""),
                                 args.get("deadline", ""),
                                 args["pic_open_id"], args.get("assigner_open_id", ""))
        return _text(f"Đã tạo việc [{row['id']}] và nhắn PIC {row.get('pic_name')}.")
    except Exception as e:
        return _err(e)


@tool("assignment_list",
      "Danh sách việc BOD giao đang mở (assigned/in_review/needs_more) kèm deadline, "
      "PIC, đầu ra mong đợi. pic_open_id: lọc theo 1 người (bỏ trống = tất cả). "
      "Dùng khi: PIC nộp kết quả (tìm việc tương ứng), đôn đốc, tổng kết cuối ngày.",
      {"pic_open_id": str})
async def assignment_list(args: dict) -> dict:
    try:
        from . import assignments
        rows = assignments.list_active((args.get("pic_open_id") or "").strip() or None)
        return _text(assignments.fmt(rows))
    except Exception as e:
        return _err(e)


@tool("assignment_update",
      "Cập nhật việc BOD giao. status: in_review (PIC vừa nộp, đang đánh giá) | "
      "needs_more (thiếu, đã yêu cầu bổ sung) | done (đủ — BẮT BUỘC kèm result_summary "
      "là bản tổng hợp cho BOD ra quyết định; hệ thống tự complete Lark task).",
      {"assignment_id": str, "status": str, "result_summary": str})
async def assignment_update(args: dict) -> dict:
    try:
        from . import assignments
        row = assignments.update(args["assignment_id"],
                                 (args.get("status") or "").strip() or None,
                                 args.get("result_summary"))
        return _text(f"Đã cập nhật [{row['id']}] → {row['status']}.")
    except Exception as e:
        return _err(e)


@tool("assignment_remind",
      "Nhắn nhắc PIC của 1 việc đang mở (đôn đốc deadline, hỏi tiến độ). "
      "message: nội dung nhắc — lịch sự, nêu rõ việc + deadline.",
      {"assignment_id": str, "message": str})
async def assignment_remind(args: dict) -> dict:
    try:
        from . import assignments
        assignments.remind(args["assignment_id"], args["message"])
        return _text("Đã nhắc PIC.")
    except Exception as e:
        return _err(e)


@tool("assignment_notify_assigner",
      "Gửi báo cáo/tổng hợp về 1 việc cho thành viên BOD đã giao việc đó (chat riêng). "
      "Dùng khi kết quả đã đủ (kèm summary + đề xuất quyết định) hoặc cần escalate "
      "(quá hạn nhiều lần, PIC không phản hồi).",
      {"assignment_id": str, "text": str})
async def assignment_notify_assigner(args: dict) -> dict:
    try:
        from . import assignments
        assignments.notify_assigner(args["assignment_id"], args["text"])
        return _text("Đã gửi cho người giao việc.")
    except Exception as e:
        return _err(e)


# ---------- NotebookLM (Gemini Notebook) ----------

@tool("notebooklm_ask",
      "Hỏi đáp dựa trên notebook tri thức của LSR trên NotebookLM (nguồn: meeting notes, "
      "báo cáo, tài liệu đã đồng bộ). Dùng khi câu hỏi cần tổng hợp sâu từ nhiều tài liệu "
      "nội bộ đã tích lũy. Trả lời có căn cứ theo nguồn trong notebook.",
      {"question": str})
async def notebooklm_ask(args: dict) -> dict:
    try:
        from . import nblm
        answer = await nblm.ask(args["question"])
        return _text(answer)
    except Exception as e:
        return _err(e)


@tool("notebooklm_add_source",
      "Thêm 1 nguồn vào notebook tri thức: url (trang web/tài liệu công khai) hoặc "
      "text (nội dung markdown, kèm title). Truyền MỘT trong hai.",
      {"url": str, "title": str, "text": str})
async def notebooklm_add_source(args: dict) -> dict:
    try:
        from . import nblm
        if (args.get("url") or "").strip():
            await nblm.add_url_source(args["url"].strip())
            return _text(f"Đã thêm nguồn: {args['url']}")
        if (args.get("text") or "").strip():
            await nblm.add_markdown_source(args.get("title") or "Tài liệu", args["text"])
            return _text("Đã thêm nguồn text vào notebook.")
        return _text("Cần truyền url hoặc text.")
    except Exception as e:
        return _err(e)


@tool("notebooklm_audio_overview",
      "Tạo Audio Overview (podcast 2 giọng thảo luận) từ notebook tri thức và gửi file "
      "vào chat hiện tại. Mất 5-15 phút — tool trả về ngay, file gửi sau khi xong. "
      "chat_id lấy từ bối cảnh hội thoại. instructions: định hướng nội dung (tùy chọn).",
      {"chat_id": str, "instructions": str})
async def notebooklm_audio_overview(args: dict) -> dict:
    chat_id = (args.get("chat_id") or "").strip()
    if not chat_id:
        return _text("Thiếu chat_id.")

    def _run() -> None:
        import asyncio as aio
        import tempfile

        from . import lark_user, nblm
        out = tempfile.mktemp(suffix=".m4a", prefix="nblm-audio-")
        try:
            aio.run(nblm.audio_overview(out, args.get("instructions", "")))
            lark_user.send_file(chat_id, out)
            lark_user.send_text(chat_id, "🎧 Audio Overview từ notebook đây ạ!")
        except Exception as e:
            try:
                lark_user.send_text(chat_id, f"⚠️ Tạo audio overview lỗi: {str(e)[:200]}")
            except Exception:
                pass
        finally:
            import os as _os
            if _os.path.exists(out):
                _os.unlink(out)

    import threading
    threading.Thread(target=_run, daemon=True).start()
    return _text("Đã bắt đầu tạo Audio Overview (5-15 phút) — xong sẽ gửi file vào chat này. "
                 "Báo người dùng chờ nhé.")


# ---------- Kho bộ nhớ ----------

@tool("memory_save",
      "Lưu nội dung .md vào kho bộ nhớ Lark Drive. subfolder: meetings|reports|market|"
      "knowledge|summaries. summary: 1 câu mô tả để ghi vào INDEX.",
      {"subfolder": str, "filename": str, "markdown": str, "summary": str})
async def memory_save(args: dict) -> dict:
    try:
        from . import lark_memory
        sub = args["subfolder"]
        if sub not in lark_memory.SUBFOLDERS:
            return _text(f"subfolder phải là một trong: {lark_memory.SUBFOLDERS}")
        fname = args["filename"] if args["filename"].endswith(".md") else args["filename"] + ".md"
        res = lark_memory.save_markdown(sub, fname, args["markdown"])
        token = res.get("file_token", "")
        lark_memory.append_index(
            f"- [{sub}/{fname}] (token:{token}) · {lark_memory.today_vn()} · {args.get('summary','')}")
        return _text(f"Đã lưu {sub}/{fname} ({res['status']})")
    except Exception as e:
        return _err(e)


@tool("memory_index",
      "Đọc INDEX kho bộ nhớ (danh mục mọi file đã lưu — mỗi dòng có token để đọc file). "
      "LUÔN đọc INDEX trước, chọn đúng file rồi mới gọi memory_read.", {})
async def memory_index(args: dict) -> dict:
    try:
        from . import lark_memory, lark_user
        cfg = lark_memory._setup()
        content = lark_user._get(f"/docx/v1/documents/{cfg['index_doc']}/raw_content") \
            .get("content", "")
        return _text(content or "(INDEX trống)")
    except Exception as e:
        return _err(e)


@tool("memory_read",
      "Đọc nội dung 1 file trong kho bộ nhớ theo token (lấy từ dòng tương ứng trong INDEX, "
      "dạng token:xxx).",
      {"file_token": str})
async def memory_read(args: dict) -> dict:
    try:
        from . import lark_user
        content = lark_user.download_file(args["file_token"]).decode("utf-8", errors="replace")
        return _text(content[:MAX_CHARS])
    except Exception as e:
        return _err(e)


lark_server = create_sdk_mcp_server(
    name="lark", version="1.0.0",
    tools=[read_lark_document,
           calendar_list_events, calendar_create_event, calendar_delete_event,
           task_create, task_list, task_complete,
           send_lark_message,
           meeting_list_pending, meeting_save_draft, meeting_finalize,
           org_lookup, person_note_save,
           search_resources, recent_resources, watch_document,
           notebooklm_ask, notebooklm_add_source, notebooklm_audio_overview,
           assignment_create, assignment_list, assignment_update,
           assignment_remind, assignment_notify_assigner,
           memory_save, memory_index, memory_read])
