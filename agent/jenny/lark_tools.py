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
      "link Lark — sẽ bị chặn đăng nhập). Nhận link wiki / docx / base / **sheets "
      "(bảng tính)**. Bảng tính trả về từng trang dưới dạng bảng markdown, tối đa 200 "
      "dòng × 30 cột mỗi trang và 8 trang; nếu bị cắt thì tool ghi rõ tổng số dòng — cần "
      "phần sau thì nói người dùng khoanh vùng lại. Chỉ cần file được SHARE cho account "
      "Jenny, KHÔNG cần add bot vào file.",
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


@tool("write_lark_document",
      "GHI nội dung vào tài liệu Lark (docx). mode='create': tạo tài liệu MỚI, cần "
      "`title`; mode='append': ghi thêm vào cuối tài liệu có sẵn, cần `url`. "
      "markdown: nội dung — hỗ trợ #/##/### tiêu đề, - gạch đầu dòng, 1. danh số, "
      "> trích dẫn, ``` khối code, --- đường kẻ, - [ ] việc cần làm. "
      "Chỉ cần tài khoản Jenny có quyền sửa tài liệu đó (được share quyền edit), KHÔNG "
      "cần add bot. Dùng khi người dùng nhờ 'viết vào file này', 'tạo tài liệu tổng hợp', "
      "'lưu brief thành doc'. LUÔN trả link cho người dùng sau khi ghi. "
      "Bảng markdown sẽ thành từng dòng chữ (Lark docx không có block bảng) — cần bảng "
      "thật thì nói người dùng dùng Sheet.",
      {"mode": str, "title": str, "url": str, "markdown": str})
async def write_lark_document(args: dict) -> dict:
    try:
        from . import lark_user
        md = args.get("markdown") or ""
        if not md.strip():
            return _text("Thiếu nội dung markdown cần ghi.")
        mode = (args.get("mode") or "").strip().lower()
        if not mode:
            mode = "append" if (args.get("url") or "").strip() else "create"

        if mode == "create":
            title = (args.get("title") or "").strip()
            if not title:
                return _text("mode='create' cần `title` cho tài liệu mới.")
            doc = lark_user.create_document(title)
            n = lark_user.append_markdown(doc["document_id"], md)
            return _text(f"Đã tạo tài liệu '{title}' và ghi {n} khối nội dung.\n"
                         f"Link: {doc['url']}")
        if mode == "append":
            url = (args.get("url") or "").strip()
            if not url:
                return _text("mode='append' cần `url` tài liệu đích.")
            n = lark_user.append_markdown(url, md)
            return _text(f"Đã ghi thêm {n} khối nội dung vào cuối tài liệu.\nLink: {url}")
        return _text("mode phải là 'create' hoặc 'append'.")
    except Exception as e:
        return _err(e)


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

def _agent_flags() -> tuple[set, list]:
    """config `known_agents`: open_ids + name_patterns để nhận ra tài khoản agent."""
    cfg = db.all_configs().get("known_agents", {}) or {}
    return (set(cfg.get("open_ids") or []),
            [str(p).lower() for p in (cfg.get("name_patterns") or [])])


@tool("group_members",
      "Liệt kê ai TAG ĐƯỢC trong group Lark kèm open_id — gồm cả NGƯỜI và BOT/AGENT. "
      "keyword: lọc theo tên (bỏ trống = liệt kê, tối đa 60 dòng). chat_id lấy từ bối cảnh "
      "hội thoại. Cách dùng: tra tên → lấy open_id → chèn `<at user_id=\"ou_xxx\"></at>` "
      "vào câu trả lời là tag được (bot app cũng tag được y như người). "
      "Nguồn dữ liệu: người thật lấy từ API thành viên; BOT/AGENT lấy từ sổ đăng ký mà "
      "Jenny học được từ lịch sử tin nhắn (vì API Lark không liệt kê bot app). "
      "Nếu tìm không thấy bot cần tag, gọi lại với discover=true để quét lại lịch sử chat.",
      {"chat_id": str, "keyword": str, "bots_only": bool, "discover": bool})
async def group_members(args: dict) -> dict:
    try:
        from . import lark_user, mentionables
        chat_id = (args.get("chat_id") or "").split("#", 1)[0].strip()
        if not chat_id:
            return _text("Thiếu chat_id.")
        kw = (args.get("keyword") or "").strip()
        bots_only = bool(args.get("bots_only"))

        if args.get("discover"):
            res = mentionables.discover(chat_id, days=180, max_pages=30)
            note = (f"(vừa quét lại {res['messages']} tin, "
                    f"thấy {len(res['bots'])} bot/agent)\n")
        else:
            note = ""

        # BOT/AGENT — từ sổ đăng ký học được (API Lark không trả bot app)
        bots = mentionables.list_for_chat(chat_id, kw, bots_only=True)
        if not bots and not args.get("discover"):
            res = mentionables.discover(chat_id, days=180, max_pages=20)
            bots = mentionables.list_for_chat(chat_id, kw, bots_only=True)
            note = (f"(chưa có sổ bot cho chat này nên em quét {res['messages']} tin "
                    f"lịch sử, thấy {len(res['bots'])} bot/agent)\n")

        lines = []
        for b in bots[:40]:
            lines.append(f"- {b['name']} 🤖 BOT/AGENT\n  open_id: {b['open_id']}")

        humans = []
        if not bots_only:
            agent_ids, agent_pats = _agent_flags()
            for m in lark_user.find_members_by_name(chat_id, kw)[:60]:
                mark = " 🤖 AGENT (tài khoản người dùng)" if (
                    m["open_id"] in agent_ids
                    or any(p in m["name"].lower() for p in agent_pats)) else ""
                humans.append(f"- {m['name']}{mark}\n  open_id: {m['open_id']}")

        if not lines and not humans:
            return _text(note + f"Không thấy ai khớp '{kw}' trong chat này.")
        out = note
        if lines:
            out += f"**BOT/AGENT ({len(bots)})**\n" + "\n".join(lines) + "\n\n"
        if humans:
            out += f"**NGƯỜI ({len(humans)})**\n" + "\n".join(humans) + "\n\n"
        return _text(out + "Tag bằng cách chèn <at user_id=\"ou_xxx\"></at> vào câu trả lời.")
    except Exception as e:
        return _err(e)


@tool("send_lark_message",
      "Gửi tin nhắn đến 1 chat Lark khác (group/người) theo chat_id. CHỈ gửi được vào chat "
      "đã whitelist. Dùng khi cần chuyển tiếp/báo cáo sang chat khác — trong hội thoại "
      "hiện tại thì trả lời bình thường, không dùng tool này. "
      "mention_open_ids: danh sách open_id cần TAG (cách nhau bằng dấu phẩy) — dùng khi "
      "cần đích danh ai đó trả lời/xử lý; tra open_id bằng org_lookup.",
      {"chat_id": str, "text": str, "mention_open_ids": str})
async def send_lark_message(args: dict) -> dict:
    try:
        from . import lark_user
        chat_id = args["chat_id"]
        res = (db.sb().table("conversations").select("whitelisted")
               .eq("channel", "lark").eq("chat_id", chat_id).execute())
        if not res.data or not res.data[0]["whitelisted"]:
            return _text("Từ chối: chat này chưa được whitelist.")
        ats = [s.strip() for s in (args.get("mention_open_ids") or "").split(",") if s.strip()]
        lark_user.send_text(chat_id, args["text"], mention_open_ids=ats or None)
        return _text("Đã gửi." + (f" Đã tag {len(ats)} người." if ats else ""))
    except Exception as e:
        return _err(e)


@tool("my_capabilities",
      "Bản khai năng lực của CHÍNH JENNY + hướng dẫn agent khác hỏi Jenny (A2A). "
      "Dùng khi được hỏi 'bạn làm được gì', 'Jenny có năng lực nào', hoặc khi MỘT AGENT "
      "KHÁC hỏi cách phối hợp/gọi Jenny. format='short' (mặc định, danh sách gọn) hoặc "
      "'full' (đầy đủ kèm ví dụ câu hỏi, giới hạn, cách gửi yêu cầu). Trả lời dựa đúng "
      "danh sách này, KHÔNG tự nhận thêm năng lực không có trong đó.",
      {"format": str})
async def my_capabilities(args: dict) -> dict:
    try:
        from . import capabilities
        if (args.get("format") or "short").lower() == "full":
            return _text(capabilities.as_markdown()[:MAX_CHARS])
        lines = [f"- **{c['name']}** (`{c['id']}`): {c['description']}"
                 for c in capabilities.CAPABILITIES]
        no = [f"- {n['id']}: {n['reason']}" for n in capabilities.NOT_SUPPORTED]
        a = capabilities.a2a_howto()
        return _text("**Jenny làm được:**\n" + "\n".join(lines)
                     + "\n\n**Không làm:**\n" + "\n".join(no)
                     + f"\n\n**Agent khác hỏi Jenny:** tag hoặc nhắn riêng open_id "
                       f"`{a['address']['open_id']}` trên Lark, câu hỏi phải tự đủ ngữ "
                       f"cảnh (Jenny không thấy hội thoại phía bạn). Chi tiết: gọi lại "
                       f"tool này với format='full'.")
    except Exception as e:
        return _err(e)


# ---------- Hỏi agent khác (agent-to-agent) ----------

@tool("ask_agent",
      "HỎI MỘT AGENT KHÁC rồi chờ nó trả lời (qua chat Lark). Dùng khi việc đó do agent "
      "khác phụ trách — quan trọng nhất: **biên bản họp / meeting notes do agent MINO "
      "làm**, Jenny KHÔNG tự gỡ băng nữa. agent: tên agent (vd 'mino'). question: câu hỏi "
      "đầy đủ, tự đủ ngữ cảnh (nêu rõ cuộc họp nào, ngày nào, cần phần gì — người kia "
      "không thấy hội thoại của mình). wait_sec: chờ tối đa (mặc định 120). "
      "Tool CHỜ ĐỒNG BỘ nên chỉ gọi 1 lần cho mỗi việc; nếu timeout thì báo người dùng là "
      "agent kia chưa phản hồi, ĐỪNG tự bịa nội dung biên bản.",
      {"agent": str, "question": str, "wait_sec": int})
async def ask_agent(args: dict) -> dict:
    try:
        from . import peers
        res = peers.ask(args["agent"], args["question"], args.get("wait_sec"))
        if res["status"] == "answered":
            return _text(f"[{res['agent']} trả lời sau {res['waited_sec']}s]\n\n"
                          f"{res['answer'][:MAX_CHARS]}")
        if res["status"] == "timeout":
            return _text(f"⏳ {res['note']}")
        return _text(f"Lỗi: {res.get('error')}")
    except Exception as e:
        return _err(e)


@tool("peer_agents_list",
      "Liệt kê các agent khác mà Jenny có thể hỏi (tên, việc phụ trách, chat).", {})
async def peer_agents_list(args: dict) -> dict:
    try:
        from . import peers
        reg = peers.registry()
        if not reg:
            return _text("Chưa khai agent nào trong config `peer_agents`.")
        return _text("\n".join(
            f"- **{k}** — phụ trách: {v.get('role') or '?'} · chat: {v.get('chat_id')}"
            + (f" · open_id: {v['open_id']}" if v.get("open_id") else " · (chưa có open_id để tag)")
            for k, v in reg.items()))
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
        # Chế độ delegate: biên bản họp do agent khác (Mino) làm → Jenny KHÔNG báo cáo
        # danh sách họp chờ nội dung nữa. Chặn ngay ở tầng tool để dù prompt của lịch
        # định kỳ có sót yêu cầu cũ thì báo cáo cũng không chèn nội dung họp vào.
        if not meetings.self_transcribe():
            return _text("Biên bản họp hiện do agent MINO phụ trách — Jenny không theo "
                         "dõi danh sách họp chờ nội dung nữa. KHÔNG đưa mục này vào báo "
                         "cáo. Cần nội dung cuộc họp thì dùng ask_agent với agent='mino'.")
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


# ---------- Lịch chạy định kỳ (tự đặt từ chat) ----------

def _cron_from_vn(minute: str, hour: str, dom: str, mon: str, dow: str) -> str:
    return f"{minute} {hour} {dom} {mon} {dow}"


@tool("schedule_create",
      "Tạo nhiệm vụ ĐỊNH KỲ cho chính Jenny khi người dùng yêu cầu 'cập nhật/nhắc/báo cáo "
      "mỗi X'. cron: cú pháp cron giờ VN (mỗi giờ='0 * * * *'; mỗi 30 phút='*/30 * * * *'; "
      "8h sáng hằng ngày='0 8 * * *'; mỗi thứ 2='0 9 * * 1'). prompt: việc Jenny sẽ làm mỗi "
      "lần chạy (mô tả rõ, độc lập, không hỏi lại). chat_id + channel(lark/telegram) = nơi "
      "gửi kết quả (lấy từ bối cảnh hội thoại hiện tại). thread_reply_to: nếu yêu cầu đến "
      "từ MỘT THREAD và muốn cập nhật vào đúng thread đó, truyền message_id (có trong bối "
      "cảnh 'thread_reply_to: ...'). until: hạn dừng (YYYY-MM-DD HH:MM giờ VN) — sau mốc này "
      "lịch tự tắt (dùng khi 'cập nhật đến 9pm hôm nay'); bỏ trống = chạy vô hạn. "
      "name: tên ngắn gợi nhớ.",
      {"name": str, "cron": str, "prompt": str, "chat_id": str, "channel": str,
       "thread_reply_to": str, "until": str})
async def schedule_create(args: dict) -> dict:
    try:
        cron = (args.get("cron") or "").strip()
        if len(cron.split()) != 5:
            return _text("cron phải có 5 trường 'phút giờ ngày tháng thứ' (giờ VN). "
                         "Mỗi giờ = '0 * * * *'.")
        chat_id = args["chat_id"]
        trt = (args.get("thread_reply_to") or "").strip()
        if trt:
            chat_id = f"{chat_id}#{trt}"  # scheduler reply vào đúng thread
        row_data = {
            "name": args["name"], "cron": cron, "prompt": args["prompt"],
            "channel": (args.get("channel") or "lark"),
            "chat_id": chat_id, "enabled": True,
        }
        until = (args.get("until") or "").strip()
        if until:
            row_data["expires_at"] = _parse_vn(until).isoformat()
        row = db.sb().table("scheduled_tasks").insert(row_data).execute().data[0]
        return _text(f"Đã đặt lịch [{row['id']}] '{args['name']}' — cron `{cron}` (giờ VN)"
                     + (" (cập nhật vào đúng thread này)." if trt else " (gửi vào chat này).")
                     + " BÂY GIỜ hãy CHẠY NGAY nhiệm vụ này một lần (thực hiện đúng phần "
                       "việc trong prompt lịch: lấy lại query bằng bq_recent_queries → chạy "
                       "bq_query → trình bày) và gửi kết quả hiện tại luôn, KHÔNG chờ chu kỳ "
                       "đầu tiên.")
    except Exception as e:
        return _err(e)


@tool("schedule_list",
      "Liệt kê các lịch chạy định kỳ hiện có (kèm id, cron, trạng thái). Lọc theo chat_id "
      "nếu truyền (bỏ trống = tất cả).", {"chat_id": str})
async def schedule_list(args: dict) -> dict:
    try:
        rows = (db.sb().table("scheduled_tasks").select("*")
                .order("created_at").execute().data)
        cid = (args.get("chat_id") or "").split("#", 1)[0].strip()
        if cid:
            # Lịch trả kết quả vào THREAD có chat_id dạng "oc_xxx#om_yyy" — so sánh
            # bằng phần chat trước dấu '#', nếu không sẽ bỏ sót đúng những lịch đó.
            rows = [r for r in rows
                    if (r.get("chat_id") or "").split("#", 1)[0] == cid]
        if not rows:
            return _text("Chưa có lịch chạy nào.")
        return _text("\n".join(
            f"- [{r['id']}] {r['name']} · cron `{r['cron']}` · "
            f"{'BẬT' if r['enabled'] else 'TẮT'} · gửi {r['channel']}/{r['chat_id']}"
            for r in rows))
    except Exception as e:
        return _err(e)


@tool("schedule_update",
      "SỬA một lịch chạy định kỳ đã có (id lấy từ schedule_list, 8 ký tự đầu cũng được). "
      "**BẮT BUỘC dùng tool này khi người dùng yêu cầu thay đổi nội dung báo cáo định kỳ** "
      "— ví dụ 'bỏ phần họp ra khỏi tổng kết cuối ngày', 'thêm phần tồn kho vào brief "
      "sáng', 'đổi giờ chạy'. Nếu chỉ trả lời 'vâng em sẽ bỏ' mà KHÔNG gọi tool này thì "
      "lần chạy sau vẫn y như cũ — đó là lỗi nghiêm trọng. "
      "prompt: nội dung MỚI thay thế toàn bộ (bỏ trống = giữ nguyên). "
      "prompt_remove: đoạn/dòng cần XOÁ khỏi prompt hiện tại (khớp một phần, không phân "
      "biệt hoa thường) — dùng khi chỉ cần bỏ một mục. "
      "prompt_append: đoạn cần THÊM vào cuối prompt. "
      "cron/name/chat_id/enabled/until: sửa nếu truyền. "
      "Sau khi sửa, ĐỌC LẠI prompt trong kết quả trả về và xác nhận với người dùng đúng "
      "phần họ muốn đổi.",
      {"schedule_id": str, "prompt": str, "prompt_remove": str, "prompt_append": str,
       "cron": str, "name": str, "chat_id": str, "enabled": bool, "until": str})
async def schedule_update(args: dict) -> dict:
    try:
        sid = (args.get("schedule_id") or "").strip()
        if not sid:
            return _text("Thiếu schedule_id.")
        rows = db.sb().table("scheduled_tasks").select("*").execute().data
        match = [r for r in rows if r["id"] == sid or r["id"].startswith(sid)]
        if not match:
            return _text(f"Không tìm thấy lịch nào có id bắt đầu bằng '{sid}'. "
                         "Gọi schedule_list để xem danh sách.")
        if len(match) > 1:
            return _text(f"'{sid}' khớp {len(match)} lịch — truyền id dài hơn.")
        row = match[0]
        patch: dict = {}

        cur_prompt = row.get("prompt") or ""
        new_prompt = cur_prompt
        if (args.get("prompt") or "").strip():
            new_prompt = args["prompt"]
        if (args.get("prompt_remove") or "").strip():
            needle = args["prompt_remove"].strip().lower()
            kept = [ln for ln in new_prompt.splitlines()
                    if needle not in ln.strip().lower()]
            if len(kept) == len(new_prompt.splitlines()):
                # không khớp theo dòng → thử bỏ đúng đoạn văn bản
                import re as _re
                new2 = _re.sub(_re.escape(args["prompt_remove"]), "", new_prompt,
                               flags=_re.I).strip()
                if new2 == new_prompt.strip():
                    return _text(f"Không thấy đoạn '{args['prompt_remove'][:60]}' trong "
                                 f"prompt hiện tại. Prompt đang là:\n\n{cur_prompt}")
                new_prompt = new2
            else:
                new_prompt = "\n".join(kept)
        if (args.get("prompt_append") or "").strip():
            new_prompt = (new_prompt.rstrip() + "\n" + args["prompt_append"].strip())
        if new_prompt != cur_prompt:
            patch["prompt"] = new_prompt.strip()

        if (args.get("cron") or "").strip():
            if len(args["cron"].split()) != 5:
                return _text("cron phải có 5 trường 'phút giờ ngày tháng thứ' (giờ VN).")
            patch["cron"] = args["cron"].strip()
        for k in ("name", "chat_id"):
            if (args.get(k) or "").strip():
                patch[k] = args[k].strip()
        if args.get("enabled") is not None:
            patch["enabled"] = bool(args["enabled"])
        if (args.get("until") or "").strip():
            patch["expires_at"] = _parse_vn(args["until"]).isoformat()

        if not patch:
            return _text(f"Không có gì để sửa. Prompt hiện tại của "
                         f"'{row['name']}':\n\n{cur_prompt}")
        res = (db.sb().table("scheduled_tasks").update(patch)
               .eq("id", row["id"]).execute()).data[0]
        changed = ", ".join(patch.keys())
        return _text(f"Đã sửa lịch [{res['id'][:8]}] '{res['name']}' (đổi: {changed}).\n\n"
                     f"PROMPT MỚI — đọc lại và xác nhận với người dùng:\n{res.get('prompt')}")
    except Exception as e:
        return _err(e)


@tool("schedule_delete",
      "Xóa (hoặc tắt) 1 lịch chạy định kỳ theo id. disable_only=true để chỉ tạm tắt.",
      {"schedule_id": str, "disable_only": bool})
async def schedule_delete(args: dict) -> dict:
    try:
        sid = args["schedule_id"]
        if args.get("disable_only"):
            db.sb().table("scheduled_tasks").update({"enabled": False}).eq("id", sid).execute()
            return _text("Đã tắt lịch (giữ lại để bật lại sau).")
        db.sb().table("scheduled_tasks").delete().eq("id", sid).execute()
        return _text("Đã xóa lịch.")
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


# ---------- Chỉ số hoàn thành cam kết ----------

@tool("assignment_stats",
      "Tỷ lệ hoàn thành cam kết của các việc BOD giao: % việc đến hạn trong kỳ được đóng "
      "ĐÚNG HẠN, số việc đang quá hạn. days: số ngày nhìn lại (mặc định 7). Dùng khi viết "
      "tổng kết cuối ngày, digest đầu tuần, hoặc chuẩn bị họp BOD — đặt con số này lên đầu.",
      {"days": int})
async def assignment_stats(args: dict) -> dict:
    try:
        from . import assignments
        s = assignments.followthrough_stats(int(args.get("days") or 7))
        if not s["total_due"]:
            return _text(f"Trong {s['period_days']} ngày qua không có việc nào đến hạn. "
                         f"Hiện có {s['overdue_now']} việc đang quá hạn.")
        lines = [
            f"Kỳ {s['period_days']} ngày: {s['total_due']} việc đến hạn.",
            f"- Đúng hạn: {s['on_time']} · Trễ: {s['late']} · Chưa xong: {s['still_open']}",
            f"- **Tỷ lệ hoàn thành đúng hạn: {s['rate_pct']}%**",
            f"- Đang quá hạn lúc này: {s['overdue_now']} việc",
        ]
        if s["overdue_titles"]:
            lines.append("  " + "; ".join(s["overdue_titles"]))
        return _text("\n".join(lines))
    except Exception as e:
        return _err(e)


# ---------- Sổ quyết định ----------

@tool("decision_log",
      "GHI 1 QUYẾT ĐỊNH của BOD vào sổ quyết định, kèm kỳ vọng đo được để sau này đối "
      "chiếu với thực tế. Dùng khi BOD chốt một việc trong họp hoặc trong chat. "
      "LUÔN xác nhận với người quyết trước khi ghi. "
      "type: big_bet (hệ trọng, hiếm) | cross_cutting (định kỳ liên phòng ban, vd S&OP) | "
      "delegated (thường xuyên, ít rủi ro). reversible: quyết định này đảo ngược được không "
      "— nếu có thì khuyên quyết nhanh, nếu không thì mới cần brief + pre-mortem đầy đủ. "
      "metric_*: chỉ số sẽ dùng để đo (vd metric_name='AOV', metric_target='8', "
      "metric_unit='%', metric_direction='up'). review_at: 'YYYY-MM-DD HH:MM' giờ VN — mốc "
      "Jenny tự đo lại. review_sql: câu SQL trả về ĐÚNG 1 dòng 1 cột tên `v` (bỏ trống thì "
      "đến hạn Jenny sẽ hỏi người quyết). confidence: mức tự tin 0-100 của người quyết.",
      {"title": str, "decider_open_id": str, "decider_name": str, "type": str,
       "reversible": bool, "context": str, "options": str, "chosen": str,
       "expected_outcome": str, "metric_name": str, "metric_target": str,
       "metric_unit": str, "metric_direction": str, "confidence": int,
       "review_at": str, "review_sql": str, "chat_id": str,
       "source_kind": str, "source_id": str})
async def decision_log(args: dict) -> dict:
    try:
        from . import decisions
        metric = None
        if (args.get("metric_name") or "").strip():
            metric = {"metric": args["metric_name"],
                      "target": args.get("metric_target"),
                      "unit": args.get("metric_unit") or "",
                      "direction": args.get("metric_direction") or "up"}
        row = decisions.create(
            title=args["title"],
            decider_open_id=args.get("decider_open_id", ""),
            decider_name=args.get("decider_name", ""),
            dtype=args.get("type") or "delegated",
            reversible=args.get("reversible"),
            context=args.get("context", ""), options=args.get("options", ""),
            chosen=args.get("chosen", ""),
            expected_outcome=args.get("expected_outcome", ""),
            expected_metric=metric,
            confidence=args.get("confidence"),
            review_at_vn=args.get("review_at", ""),
            review_sql=args.get("review_sql", ""),
            source_kind=args.get("source_kind") or "chat",
            source_id=args.get("source_id", ""),
            chat_id=args.get("chat_id", ""))
        msg = f"Đã ghi quyết định [{row['id'][:8]}] '{row['title']}' vào sổ."
        if row.get("review_at"):
            msg += f" Em sẽ tự đo kết quả vào {str(row['review_at'])[:16]}."
        elif not (args.get("review_at") or "").strip():
            msg += (" CHƯA có mốc đo kết quả — hãy hỏi người quyết bao giờ nên nhìn lại "
                    "việc này rồi cập nhật bằng decision_update.")
        return _text(msg)
    except Exception as e:
        return _err(e)


@tool("decision_list",
      "Danh sách quyết định trong sổ (đang mở). decider_open_id: lọc theo người quyết "
      "(bỏ trống = tất cả). Dùng khi cần nhìn lại 'ta đã quyết gì', chuẩn bị họp, hoặc "
      "khi ai đó hỏi về một quyết định cũ.",
      {"decider_open_id": str})
async def decision_list(args: dict) -> dict:
    try:
        from . import decisions
        rows = decisions.list_open((args.get("decider_open_id") or "").strip() or None)
        return _text(decisions.fmt(rows))
    except Exception as e:
        return _err(e)


@tool("decision_update",
      "Cập nhật 1 quyết định trong sổ theo id (8 ký tự đầu cũng được). Dùng để bổ sung mốc "
      "đo kết quả (review_at/review_sql), ghi kết quả thực tế (actual_outcome + "
      "outcome_verdict: dat|khong_dat|mot_phan), hoặc đóng/hủy (status: reviewed|cancelled).",
      {"decision_id": str, "review_at": str, "review_sql": str, "actual_outcome": str,
       "outcome_verdict": str, "status": str, "expected_outcome": str, "confidence": int})
async def decision_update(args: dict) -> dict:
    try:
        from . import decisions
        sid = args["decision_id"].strip()
        if len(sid) < 32:  # cho phép truyền 8 ký tự đầu
            rows = db.sb().table("decisions").select("id").execute().data
            match = [r["id"] for r in rows if r["id"].startswith(sid)]
            if not match:
                return _text(f"Không tìm thấy quyết định nào có id bắt đầu bằng '{sid}'.")
            sid = match[0]
        patch = {}
        if (args.get("review_at") or "").strip():
            patch["review_at"] = _parse_vn(args["review_at"]).isoformat()
        for k in ("review_sql", "actual_outcome", "outcome_verdict", "status",
                  "expected_outcome"):
            if (args.get(k) or "").strip():
                patch[k] = args[k]
        if args.get("confidence") is not None:
            patch["confidence"] = args["confidence"]
        row = decisions.update(sid, **patch)
        return _text(f"Đã cập nhật quyết định [{row['id'][:8]}] '{row['title']}'.")
    except Exception as e:
        return _err(e)


# ---------- Giám sát số liệu (anomaly / signpost) ----------

@tool("monitor_create",
      "Tạo phép GIÁM SÁT SỐ LIỆU tự động. kind='anomaly': cảnh báo khi số liệu lệch bất "
      "thường — `sql` phải trả 2 cột d (DATE) và v (số), 1 dòng/ngày, phủ ít nhất 8 tuần "
      "gần nhất (hệ thống tự so với cùng thứ trong tuần, dùng median+MAD). "
      "kind='signpost': ngưỡng kích hoạt review kế hoạch — `sql` trả 1 dòng 1 cột v; "
      "báo khi **v <= threshold_value** (direction=down) hoặc **v >= threshold_value** "
      "(direction=up). Chọn ngưỡng sao cho mức BÌNH THƯỜNG KHÔNG chạm: nếu bình thường "
      "là 4 và muốn báo khi thiếu, đặt threshold_value=3 (không phải 4). "
      "direction: both|down|up. Với chỉ số kinh doanh (doanh thu, đơn hàng) hầu như luôn "
      "dùng **down** — tăng mạnh do campaign KHÔNG phải điều BOD cần bị đánh thức. "
      "check_cron: giờ VN, mặc định '0 9,15,21 * * *'; nếu chỉ số tính theo NGÀY thì SQL "
      "phải loại trừ hôm nay (ngày chưa trọn sẽ luôn trông như tụt mạnh). "
      "chat_id lấy từ bối cảnh hội thoại. "
      "LUÔN chạy thử SQL bằng bq_query trước để chắc chắn đúng cột và có dữ liệu.",
      {"name": str, "sql": str, "metric_label": str, "unit": str, "chat_id": str,
       "kind": str, "direction": str, "check_cron": str, "threshold_high": str,
       "threshold_med": str, "threshold_value": str, "cooldown_hours": int, "note": str})
async def monitor_create(args: dict) -> dict:
    try:
        row = {
            "name": args["name"], "sql": args["sql"],
            "metric_label": args.get("metric_label") or args["name"],
            "unit": args.get("unit", ""), "chat_id": args["chat_id"],
            "kind": args.get("kind") or "anomaly",
            "direction": args.get("direction") or "both",
            "check_cron": args.get("check_cron") or "0 9,15,21 * * *",
            "note": args.get("note", ""), "enabled": True,
        }
        for k in ("threshold_high", "threshold_med", "threshold_value"):
            if (args.get(k) or "").strip():
                row[k] = float(args[k])
        if args.get("cooldown_hours"):
            row["cooldown_hours"] = int(args["cooldown_hours"])
        res = db.sb().table("monitors").insert(row).execute().data[0]
        return _text(f"Đã tạo giám sát [{res['id'][:8]}] '{res['name']}' "
                     f"({res['kind']}, chạy theo cron `{res['check_cron']}` giờ VN). "
                     "Mức cao sẽ nhắn ngay, mức trung bình gom vào digest.")
    except Exception as e:
        return _err(e)


@tool("monitor_list", "Liệt kê các phép giám sát số liệu đang có (anomaly + signpost).", {})
async def monitor_list(args: dict) -> dict:
    try:
        rows = db.sb().table("monitors").select("*").order("created_at").execute().data
        if not rows:
            return _text("Chưa có phép giám sát nào.")
        return _text("\n".join(
            f"- [{r['id'][:8]}] {r['name']} · {r['kind']} · "
            f"{'BẬT' if r['enabled'] else 'TẮT'} · cron `{r['check_cron']}` · "
            f"lần cuối kiểm: {str(r.get('last_checked_at'))[:16] or 'chưa'}"
            for r in rows))
    except Exception as e:
        return _err(e)


@tool("monitor_delete",
      "Xóa (hoặc tắt) 1 phép giám sát theo id. disable_only=true để chỉ tạm tắt.",
      {"monitor_id": str, "disable_only": bool})
async def monitor_delete(args: dict) -> dict:
    try:
        sid = args["monitor_id"].strip()
        if len(sid) < 32:
            rows = db.sb().table("monitors").select("id").execute().data
            match = [r["id"] for r in rows if r["id"].startswith(sid)]
            if not match:
                return _text(f"Không tìm thấy giám sát có id bắt đầu bằng '{sid}'.")
            sid = match[0]
        if args.get("disable_only"):
            db.sb().table("monitors").update({"enabled": False}).eq("id", sid).execute()
            return _text("Đã tắt phép giám sát (giữ lại để bật lại sau).")
        db.sb().table("monitors").delete().eq("id", sid).execute()
        return _text("Đã xóa phép giám sát.")
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
    tools=[read_lark_document, write_lark_document,
           calendar_list_events, calendar_create_event, calendar_delete_event,
           task_create, task_list, task_complete,
           send_lark_message,
           meeting_list_pending, meeting_save_draft, meeting_finalize,
           ask_agent, peer_agents_list, my_capabilities,
           org_lookup, person_note_save,
           search_resources, recent_resources, watch_document, group_members,
           notebooklm_ask, notebooklm_add_source, notebooklm_audio_overview,
           assignment_create, assignment_list, assignment_update,
           assignment_remind, assignment_notify_assigner,
           schedule_create, schedule_list, schedule_update, schedule_delete,
           assignment_stats,
           decision_log, decision_list, decision_update,
           monitor_create, monitor_list, monitor_delete,
           memory_save, memory_index, memory_read])
