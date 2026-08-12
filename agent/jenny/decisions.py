"""Sổ quyết định — ghi quyết định kèm kỳ vọng đo được, đến hạn tự đo kết quả thật.

Vòng lặp (theo RESEARCH.md): ghi quyết định + dự đoán → theo dõi → đến hạn Jenny
tự chạy lại số liệu → đối chiếu kỳ vọng vs thực tế → tích lũy dữ liệu calibration
cho từng người quyết.

Điểm mấu chốt: con người không bao giờ duy trì nổi sổ này; AI thì có.
"""
from __future__ import annotations

import datetime as dt
import logging

from . import db

log = logging.getLogger(__name__)

VN = dt.timezone(dt.timedelta(hours=7))
TYPES = ("big_bet", "cross_cutting", "delegated")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _parse_vn(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.strip().replace(" ", "T")).replace(tzinfo=VN)


def create(title: str, decider_open_id: str, decider_name: str = "",
           dtype: str = "delegated", reversible: bool | None = None,
           context: str = "", options: str = "", chosen: str = "",
           expected_outcome: str = "", expected_metric: dict | None = None,
           confidence: int | None = None, review_at_vn: str = "",
           review_sql: str = "", source_kind: str = "chat", source_id: str = "",
           chat_id: str = "") -> dict:
    row = {
        "title": title, "decider_open_id": decider_open_id,
        "decider_name": decider_name,
        "type": dtype if dtype in TYPES else "delegated",
        "context_md": context, "options_md": options, "chosen_option": chosen,
        "expected_outcome": expected_outcome, "review_sql": review_sql,
        "source_kind": source_kind, "source_id": source_id, "chat_id": chat_id,
    }
    if reversible is not None:
        row["reversible"] = reversible
    if expected_metric:
        row["expected_metric"] = expected_metric
    if confidence is not None:
        row["confidence"] = max(0, min(100, int(confidence)))
    if (review_at_vn or "").strip():
        row["review_at"] = _parse_vn(review_at_vn).isoformat()
    return db.sb().table("decisions").insert(row).execute().data[0]


def list_open(decider_open_id: str | None = None, limit: int = 50) -> list[dict]:
    q = db.sb().table("decisions").select("*").eq("status", "open")
    if decider_open_id:
        q = q.eq("decider_open_id", decider_open_id)
    return q.order("decided_at", desc=True).limit(limit).execute().data


def get(decision_id: str) -> dict | None:
    res = db.sb().table("decisions").select("*").eq("id", decision_id).execute()
    return res.data[0] if res.data else None


def update(decision_id: str, **patch) -> dict:
    allowed = {"title", "type", "reversible", "context_md", "options_md",
               "chosen_option", "expected_outcome", "expected_metric", "confidence",
               "review_at", "review_sql", "actual_outcome", "actual_value",
               "outcome_verdict", "status", "chat_id"}
    clean = {k: v for k, v in patch.items() if k in allowed and v is not None}
    if not clean:
        return get(decision_id) or {}
    return (db.sb().table("decisions").update(clean)
            .eq("id", decision_id).execute()).data[0]


def fmt(rows: list[dict]) -> str:
    if not rows:
        return "Chưa có quyết định nào trong sổ."
    now = dt.datetime.now(VN)
    label = {"big_bet": "quyết định lớn", "cross_cutting": "liên phòng ban",
             "delegated": "ủy quyền"}
    out = []
    for d in rows:
        line = (f"- [{d['id'][:8]}] **{d['title']}** · {label.get(d.get('type'), d.get('type'))}"
                f" · người quyết: {d.get('decider_name') or '?'}"
                f" · ngày {str(d.get('decided_at'))[:10]}")
        if d.get("reversible") is not None:
            line += " · " + ("đảo ngược được" if d["reversible"] else "KHÔNG đảo ngược được")
        if d.get("expected_outcome"):
            line += f"\n  Kỳ vọng: {d['expected_outcome'][:180]}"
            if d.get("confidence"):
                line += f" (tự tin {d['confidence']}%)"
        if d.get("review_at"):
            r = dt.datetime.fromisoformat(d["review_at"]).astimezone(VN)
            days = (r - now).days
            line += f"\n  Đo kết quả: {r.strftime('%d/%m/%Y')}" + (
                f" (còn {days} ngày)" if days >= 0 else f" (quá {-days} ngày)")
        if d.get("actual_outcome"):
            line += f"\n  Thực tế: {d['actual_outcome'][:180]}"
        out.append(line)
    return "\n".join(out)


# ---------- Đến hạn: tự đo kết quả ----------

_last_review = 0.0
REVIEW_EVERY_SEC = 1800


def _measure(sql: str) -> float | None:
    """Chạy review_sql — kỳ vọng trả 1 dòng, 1 cột `v`."""
    from .anomaly import _bq_rows

    rows = _bq_rows(sql, max_rows=5)
    if not rows:
        return None
    v = rows[0].get("v")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def _review_one(d: dict) -> None:
    from . import agent
    from .scheduler import _send_result

    actual = None
    if (d.get("review_sql") or "").strip():
        try:
            actual = _measure(d["review_sql"])
        except Exception:
            log.exception("Đo kết quả quyết định '%s' lỗi", d["title"])

    metric = d.get("expected_metric") or {}
    if actual is None:
        # Không đo tự động được → hỏi thẳng người quyết thay vì im lặng
        text = (f"📋 Đến hạn nhìn lại quyết định: **{d['title']}**\n"
                f"- Quyết ngày {str(d.get('decided_at'))[:10]}\n"
                f"- Kỳ vọng khi đó: {d.get('expected_outcome') or '(chưa ghi)'}"
                + (f" (tự tin {d['confidence']}%)" if d.get("confidence") else "")
                + "\n\nKết quả thực tế ra sao ạ? Anh/chị cho em biết để em ghi vào sổ "
                  "quyết định — sau này em tổng hợp lại xem dự báo của mình sát tới đâu.")
        _send_result(d.get("channel") or "lark", d.get("chat_id") or "", text)
        update(d["id"], status="open")
        db.sb().table("decisions").update(
            {"review_at": (dt.datetime.now(VN) + dt.timedelta(days=7)).isoformat()}
        ).eq("id", d["id"]).execute()  # hỏi lại sau 1 tuần nếu chưa ai trả lời
        return

    target = metric.get("target")
    unit = metric.get("unit") or ""
    verdict = None
    if target is not None:
        try:
            direction = (metric.get("direction") or "up").lower()
            hit = actual >= float(target) if direction == "up" else actual <= float(target)
            ratio = actual / float(target) if float(target) else 0
            verdict = "dat" if hit else ("mot_phan" if 0.7 <= ratio <= 1.3 else "khong_dat")
        except (TypeError, ValueError, ZeroDivisionError):
            verdict = None

    prompt = (
        f"[ĐO KẾT QUẢ QUYẾT ĐỊNH]\n"
        f"Quyết định: {d['title']} (ngày {str(d.get('decided_at'))[:10]}, "
        f"người quyết: {d.get('decider_name') or '?'})\n"
        f"Kỳ vọng khi quyết: {d.get('expected_outcome')}"
        + (f" — mục tiêu {target} {unit}" if target is not None else "")
        + (f", tự tin {d['confidence']}%" if d.get("confidence") else "") + "\n"
        f"Số đo thực tế hôm nay: {actual:,.2f} {unit}\n\n"
        "Viết 3-4 câu tiếng Việt cho BOD: kết quả so với kỳ vọng thế nào, "
        "chênh ở đâu, và RÚT RA ĐIỀU GÌ cho các quyết định tương tự sau này. "
        "Được phép chạy bq_query để bóc tách thêm nếu cần. "
        "Không chào hỏi, kết bằng 1 bài học hoặc 1 khuyến nghị.")
    try:
        reply = await agent.run(prompt, agent.build_system_prompt(d["title"], True,
                                                                 channel="lark"))
        narrative = (reply.text or "").strip()
    except Exception:
        log.exception("Viết diễn giải kết quả lỗi")
        narrative = ""

    icon = {"dat": "✅", "mot_phan": "🟡", "khong_dat": "🔴"}.get(verdict or "", "📋")
    head = (f"{icon} Đo kết quả quyết định: **{d['title']}**\n"
            f"- Kỳ vọng: {d.get('expected_outcome') or '(chưa ghi)'}"
            + (f" (mục tiêu {target} {unit})" if target is not None else "") + "\n"
            f"- Thực tế: {actual:,.2f} {unit}")
    _send_result(d.get("channel") or "lark", d.get("chat_id") or "",
                 head + (f"\n\n{narrative}" if narrative else ""))

    update(d["id"], actual_value=actual, actual_outcome=narrative[:2000] or f"{actual}",
           outcome_verdict=verdict, status="reviewed")
    log.info("Đã đo kết quả quyết định '%s': %s", d["title"], verdict)


def maybe_review() -> None:
    """Quyết định đến hạn review → tự đo lại. Gọi mỗi vòng scheduler (gate 30 phút)."""
    import asyncio
    import time

    global _last_review
    if time.time() - _last_review < REVIEW_EVERY_SEC:
        return
    _last_review = time.time()

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        rows = (db.sb().table("decisions").select("*")
                .eq("status", "open").not_.is_("review_at", "null")
                .lte("review_at", now_iso).limit(5).execute()).data
    except Exception as e:
        log.warning("Không đọc được bảng decisions: %s", e)
        return

    for d in rows:
        if not d.get("chat_id"):
            log.info("Quyết định '%s' chưa có chat_id để báo — bỏ qua", d["title"])
            continue
        try:
            asyncio.run(_review_one(d))
        except Exception:
            log.exception("Review quyết định '%s' lỗi", d.get("title"))


# ---------- Calibration (bật khi đã đủ dữ liệu) ----------

def calibration(decider_open_id: str, months: int = 6) -> dict:
    """Đối chiếu mức tự tin đã khai vs tỷ lệ đạt thực tế của 1 người quyết."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=months * 30)).isoformat()
    rows = (db.sb().table("decisions").select("*")
            .eq("decider_open_id", decider_open_id).eq("status", "reviewed")
            .gte("decided_at", since).execute()).data
    scored = [r for r in rows if r.get("confidence") and r.get("outcome_verdict")]
    if not scored:
        return {"n": 0}
    hit = sum(1 for r in scored if r["outcome_verdict"] == "dat")
    avg_conf = sum(r["confidence"] for r in scored) / len(scored)
    actual = hit / len(scored) * 100
    return {
        "n": len(scored), "avg_confidence": round(avg_conf),
        "actual_hit_rate": round(actual),
        "gap_pp": round(avg_conf - actual),
        "reading": ("dự báo đang lạc quan hơn thực tế" if avg_conf - actual > 10
                    else "dự báo đang thận trọng hơn thực tế" if actual - avg_conf > 10
                    else "mức tự tin khá sát thực tế"),
    }
