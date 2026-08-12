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
        if status == "done":
            patch["completed_at"] = _now_iso()
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


def followthrough_stats(days: int = 7) -> dict:
    """Tỷ lệ hoàn thành cam kết: % việc ĐẾN HẠN trong kỳ được đóng đúng hạn.

    Một con số duy nhất nhưng thay đổi hành vi — đưa lên đầu tổng kết & digest.
    """
    now = dt.datetime.now(VN)
    since = (now - dt.timedelta(days=days)).isoformat()
    rows = (db.sb().table("assignments").select("*")
            .gte("deadline", since).lte("deadline", now.isoformat())
            .execute()).data

    total = len(rows)
    on_time = late = still_open = 0
    for a in rows:
        deadline = dt.datetime.fromisoformat(a["deadline"]).astimezone(VN)
        if a.get("status") == "done":
            done_at = a.get("completed_at") or a.get("updated_at")
            try:
                d = dt.datetime.fromisoformat(str(done_at).replace("Z", "+00:00")).astimezone(VN)
            except Exception:
                d = now
            on_time += 1 if d <= deadline else 0
            late += 0 if d <= deadline else 1
        else:
            still_open += 1

    overdue_open = [a for a in list_active()
                    if a.get("deadline")
                    and dt.datetime.fromisoformat(a["deadline"]).astimezone(VN) < now]
    return {
        "period_days": days, "total_due": total,
        "on_time": on_time, "late": late, "still_open": still_open,
        "rate_pct": round(on_time / total * 100) if total else None,
        "overdue_now": len(overdue_open),
        "overdue_titles": [f"{a['title']} ({a.get('pic_name') or '?'})"
                           for a in overdue_open[:5]],
    }


# ---------- Đôn đốc tự động ----------

_last_chase = 0.0
CHASE_EVERY_SEC = 600


def _chase_cfg() -> dict:
    cfg = db.all_configs().get("assignment_chase", {}) or {}
    return {
        "enabled": cfg.get("enabled", True),
        "before_due_hours": int(cfg.get("before_due_hours", 24)),
        "overdue_every_hours": int(cfg.get("overdue_every_hours", 24)),
        "max_overdue_reminders": int(cfg.get("max_overdue_reminders", 3)),
        "escalate_after": int(cfg.get("escalate_after", 3)),
        "quiet_hours": cfg.get("quiet_hours", [21, 7]),
    }


def _hours_since(ts: str | None, now: dt.datetime) -> float:
    if not ts:
        return 1e9
    try:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(VN)
    except Exception:
        return 1e9
    return (now - t).total_seconds() / 3600


def maybe_chase() -> None:
    """Nhắc PIC việc sắp đến hạn/quá hạn, escalate lên người giao khi cần.

    Gộp mọi việc của cùng 1 PIC vào MỘT tin nhắn để không spam.
    Gọi mỗi vòng scheduler — tự gate 10 phút/lần.
    """
    import time

    from . import anomaly

    global _last_chase
    if time.time() - _last_chase < CHASE_EVERY_SEC:
        return
    _last_chase = time.time()

    cfg = _chase_cfg()
    if not cfg["enabled"]:
        return
    now = dt.datetime.now(VN)
    if anomaly.in_quiet_hours(now, cfg["quiet_hours"]):
        return

    try:
        rows = list_active()
    except Exception as e:
        log.warning("Không đọc được assignments: %s", e)
        return

    by_pic: dict[str, list[tuple[dict, str]]] = {}
    escalations: list[dict] = []
    for a in rows:
        if not a.get("deadline"):
            continue
        deadline = dt.datetime.fromisoformat(a["deadline"]).astimezone(VN)
        hours_left = (deadline - now).total_seconds() / 3600
        count = int(a.get("reminder_count") or 0)
        since_last = _hours_since(a.get("last_reminded_at"), now)

        if 0 < hours_left <= cfg["before_due_hours"] and count == 0:
            by_pic.setdefault(a["pic_open_id"], []).append(
                (a, f"⏰ còn {hours_left:.0f} giờ tới hạn ({deadline.strftime('%d/%m %H:%M')})"))
        elif hours_left <= 0:
            if count >= cfg["escalate_after"] and not a.get("escalated_at"):
                escalations.append(a)
            elif (count < cfg["max_overdue_reminders"]
                  and since_last >= cfg["overdue_every_hours"]):
                by_pic.setdefault(a["pic_open_id"], []).append(
                    (a, f"🔴 QUÁ HẠN {-hours_left / 24:.0f} ngày "
                        f"(hạn {deadline.strftime('%d/%m %H:%M')})"))

    from . import meetings
    for pic_id, items in by_pic.items():
        lines = [f"- **{a['title']}** — {note}\n  Đầu ra mong đợi: "
                 f"{(a.get('expected_outcome') or '')[:150]}" for a, note in items]
        msg = ("📌 Em nhắc anh/chị các việc BOD giao đang tới hạn ạ:\n\n"
               + "\n".join(lines)
               + "\n\nAnh/chị nhắn em kết quả hoặc cho em biết vướng ở đâu để em báo lại BOD nhé.")
        try:
            meetings.message_user(pic_id, msg)
            for a, _ in items:
                db.sb().table("assignments").update({
                    "last_reminded_at": _now_iso(),
                    "reminder_count": int(a.get("reminder_count") or 0) + 1,
                }).eq("id", a["id"]).execute()
            log.info("Đã nhắc PIC %s về %d việc", pic_id, len(items))
        except Exception:
            log.exception("Nhắc PIC %s lỗi", pic_id)

    for a in escalations:
        deadline = dt.datetime.fromisoformat(a["deadline"]).astimezone(VN)
        text = (f"⚠️ Việc giao quá hạn nhiều lần, em xin ý kiến anh/chị:\n\n"
                f"**{a['title']}**\n"
                f"- PIC: {a.get('pic_name') or a['pic_open_id']}\n"
                f"- Hạn: {deadline.strftime('%d/%m/%Y %H:%M')} "
                f"(quá {(now - deadline).days} ngày)\n"
                f"- Em đã nhắc {a.get('reminder_count')} lần, chưa có kết quả.\n\n"
                "Anh/chị muốn: gia hạn, đổi người phụ trách, hay hủy việc này ạ?")
        try:
            notify_assigner(a["id"], text)
            db.sb().table("assignments").update(
                {"escalated_at": _now_iso()}).eq("id", a["id"]).execute()
            log.info("Đã escalate việc '%s' lên người giao", a["title"])
        except Exception:
            log.exception("Escalate việc %s lỗi", a["id"])


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
