"""Việc BOD giao (assignments) — tạo, theo dõi, đánh giá, báo cáo lại BOD.

Luồng: BOD giao việc trong chat → Jenny chuẩn hóa (context, outcome, deadline,
PIC) → tạo Lark task + hồ sơ assignments + nhắn PIC trực tiếp → PIC nộp kết quả
cho Jenny → Jenny đánh giá theo expected_outcome → đủ thì summarize gửi BOD.
"""
from __future__ import annotations

import datetime as dt
import logging

from . import db, lark_user

log = logging.getLogger(__name__)

VN = dt.timezone(dt.timedelta(hours=7))
ACTIVE = ["assigned", "in_review", "needs_more"]


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def create(title: str, context: str, expected_outcome: str, deadline_vn: str,
           pic_open_id: str, assigner_open_id: str) -> dict:
    from . import meetings, org

    pic = org.get_person(pic_open_id) or {}
    assigner = org.get_person(assigner_open_id) or {}
    deadline_iso = None
    due_ms = None
    if (deadline_vn or "").strip():
        d = dt.datetime.fromisoformat(deadline_vn.strip().replace(" ", "T")).replace(tzinfo=VN)
        deadline_iso = d.isoformat()
        due_ms = int(d.timestamp() * 1000)

    guid = ""
    try:
        task = lark_user.create_task(
            f"[BOD giao] {title}", due_ms,
            description=f"Bối cảnh: {context}\nĐầu ra mong đợi: {expected_outcome}\n"
                        f"Người giao: {assigner.get('name') or assigner_open_id}",
            assignee_open_id=pic_open_id)
        guid = task.get("guid", "")
    except Exception as e:
        log.warning("Không tạo được Lark task: %s", e)

    row = db.sb().table("assignments").insert({
        "title": title, "context": context, "expected_outcome": expected_outcome,
        "deadline": deadline_iso,
        "assigner_open_id": assigner_open_id,
        "assigner_name": assigner.get("name") or "",
        "pic_open_id": pic_open_id, "pic_name": pic.get("name") or "",
        "lark_task_guid": guid,
    }).execute().data[0]

    meetings.message_user(pic_open_id, (
        f"📌 Anh/chị được BOD ({assigner.get('name') or 'BOD'}) giao việc:\n\n"
        f"**{title}**\n"
        f"- Bối cảnh: {context}\n"
        f"- Đầu ra mong đợi: {expected_outcome}\n"
        + (f"- Deadline: {deadline_vn} (giờ VN)\n" if deadline_iso else "")
        + f"- Task đã tạo trên Lark Tasks của anh/chị.\n\n"
        "Khi có kết quả, anh/chị nhắn trực tiếp cho em (hoặc tag em trong group) "
        "— em sẽ đối chiếu với yêu cầu và tổng hợp báo cáo lại BOD ạ."))
    return row


def list_active(pic_open_id: str | None = None) -> list[dict]:
    q = db.sb().table("assignments").select("*").in_("status", ACTIVE)
    if pic_open_id:
        q = q.eq("pic_open_id", pic_open_id)
    return q.order("deadline", desc=False).limit(50).execute().data


def get(assignment_id: str) -> dict | None:
    res = db.sb().table("assignments").select("*").eq("id", assignment_id).execute()
    return res.data[0] if res.data else None


def update(assignment_id: str, status: str | None = None,
           result_summary: str | None = None) -> dict:
    patch: dict = {"updated_at": _now_iso()}
    if status:
        patch["status"] = status
    if result_summary is not None:
        patch["result_summary"] = result_summary
    row = (db.sb().table("assignments").update(patch)
           .eq("id", assignment_id).execute()).data[0]
    if status == "done" and row.get("lark_task_guid"):
        try:
            lark_user.complete_task(row["lark_task_guid"])
        except Exception:
            log.warning("Không complete được Lark task %s", row["lark_task_guid"])
    return row


def remind(assignment_id: str, message: str) -> None:
    from . import meetings
    row = get(assignment_id)
    if not row:
        raise RuntimeError("Không thấy assignment này")
    meetings.message_user(row["pic_open_id"], message)
    db.sb().table("assignments").update(
        {"last_reminded_at": _now_iso()}).eq("id", assignment_id).execute()


def notify_assigner(assignment_id: str, text: str) -> None:
    from . import meetings
    row = get(assignment_id)
    if not row or not row.get("assigner_open_id"):
        raise RuntimeError("Không thấy assignment/người giao")
    meetings.message_user(row["assigner_open_id"], text)


def fmt(rows: list[dict]) -> str:
    if not rows:
        return "Không có việc nào."
    now = dt.datetime.now(VN)
    lines = []
    for a in rows:
        dl = ""
        if a.get("deadline"):
            d = dt.datetime.fromisoformat(a["deadline"]).astimezone(VN)
            days = (d - now).days
            dl = f" · hạn {d.strftime('%d/%m %H:%M')}" + \
                 (f" (QUÁ HẠN {-days} ngày)" if d < now else f" (còn {days} ngày)")
        lines.append(f"- [{a['id']}] {a['title']} · PIC: {a.get('pic_name') or a['pic_open_id']}"
                     f"{dl} · trạng thái: {a['status']} · giao bởi: {a.get('assigner_name')}\n"
                     f"  Đầu ra mong đợi: {(a.get('expected_outcome') or '')[:200]}")
    return "\n".join(lines)
