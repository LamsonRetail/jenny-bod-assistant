"""Cảnh báo bất thường số liệu — thống kê phát hiện, LLM chỉ diễn giải.

Nguyên tắc (theo RESEARCH.md): KHÔNG dùng LLM để "ngửi" bất thường — dễ bịa và
không nhất quán. Module tự chạy SQL, so với baseline **cùng thứ trong tuần** của
các tuần gần nhất (bán lẻ có mùa vụ theo thứ rất mạnh), dùng **median + MAD** để
ngày campaign không kéo lệch baseline. LLM chỉ viết phần "vì sao / nên làm gì"
sau khi đã xác định có bất thường.

Đẩy theo tầng độ khẩn: cao → nhắn ngay · trung bình → gom vào digest ·
thấp → chỉ ghi log.
"""
from __future__ import annotations

import datetime as dt
import logging
import statistics
from typing import Any

from . import db

log = logging.getLogger(__name__)

VN = dt.timezone(dt.timedelta(hours=7))
MAD_TO_SIGMA = 0.6745  # hệ số quy đổi MAD → độ lệch chuẩn (phân phối chuẩn)


# ---------- cấu hình ----------

def _defaults() -> dict:
    """config `anomaly_defaults` — sửa trên dashboard, không cần deploy."""
    cfg = db.all_configs().get("anomaly_defaults", {}) or {}
    return {
        "enabled": cfg.get("enabled", True),
        "quiet_hours": cfg.get("quiet_hours", [21, 7]),   # [từ, đến) giờ VN
        # Ngày không cảnh báo (campaign/lễ) — 'MM-DD' lặp hằng năm hoặc 'YYYY-MM-DD'
        "blackout_dates": cfg.get("blackout_dates", [
            "01-01", "04-30", "05-01", "09-02",
            "08-08", "09-09", "10-10", "11-11", "12-12",
        ]),
        "min_history": int(cfg.get("min_history", 3)),    # tối thiểu bao nhiêu mốc baseline
        # Biến động tự nhiên tối thiểu giả định (5%) — chống báo động giả ở chuỗi phẳng
        "noise_floor_pct": float(cfg.get("noise_floor_pct", 0.05)),
        "narrative": cfg.get("narrative", True),          # có gọi LLM diễn giải không
    }


def _in_blackout(day: dt.date, defaults: dict) -> bool:
    keys = {day.strftime("%m-%d"), day.strftime("%Y-%m-%d")}
    return bool(keys & set(defaults.get("blackout_dates") or []))


def in_quiet_hours(now: dt.datetime | None = None, quiet: list | None = None) -> bool:
    """Giờ im lặng (mặc định 21h–7h) — dùng chung cho các luồng chủ động."""
    now = now or dt.datetime.now(VN)
    if quiet is None:
        quiet = _defaults()["quiet_hours"]
    try:
        start, end = int(quiet[0]), int(quiet[1])
    except Exception:
        return False
    h = now.hour
    if start > end:            # khoảng qua nửa đêm, vd 21 → 7
        return h >= start or h < end
    return start <= h < end


# ---------- chạy SQL ----------

def _bq_rows(sql: str, max_rows: int = 400) -> list[dict]:
    from google.cloud import bigquery

    cfg = db.all_configs().get("bq_data_dictionary", {})
    client = bigquery.Client(project=cfg.get("project_id") or None)
    job_config = bigquery.QueryJobConfig(maximum_bytes_billed=20 * 1024**3)
    rows = list(client.query(sql, job_config=job_config).result(max_results=max_rows))
    return [dict(r) for r in rows]


# ---------- thống kê ----------

def _to_date(v: Any) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def robust_stats(values: list[float]) -> tuple[float, float]:
    """Trả (median, MAD). MAD bền với ngoại lai hơn độ lệch chuẩn."""
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    return med, mad


def evaluate(rows: list[dict], method: str = "weekday_median",
             min_history: int = 3, noise_floor_pct: float = 0.05) -> dict | None:
    """So giá trị mới nhất với baseline. Trả None nếu không đủ dữ liệu.

    weekday_median: baseline = các ngày CÙNG THỨ trước đó (mặc định — bán lẻ).
    rolling_median: baseline = các ngày liền trước.
    """
    series = []
    for r in rows:
        d = _to_date(r.get("d"))
        v = r.get("v")
        if d is None or v is None:
            continue
        try:
            series.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    if len(series) < min_history + 1:
        return None

    series.sort(key=lambda x: x[0])
    cur_date, cur_val = series[-1]
    history = series[:-1]
    if method == "weekday_median":
        history = [s for s in history if s[0].weekday() == cur_date.weekday()]
    history = history[-8:]  # tối đa 8 mốc gần nhất
    if len(history) < min_history:
        return None

    vals = [v for _, v in history]
    med, mad = robust_stats(vals)
    # Sàn nhiễu: giả định chỉ số luôn dao động tự nhiên ít nhất `noise_floor_pct`
    # quanh trung vị. Không có sàn này thì chuỗi quá phẳng (MAD ≈ 0) sẽ khiến lệch
    # 1% cũng bị thổi thành z rất lớn → báo động giả liên tục.
    scale = max(mad, noise_floor_pct * abs(med))
    z = MAD_TO_SIGMA * (cur_val - med) / scale if scale > 0 else 0.0
    pct = ((cur_val - med) / med * 100) if med else None
    return {
        "date": cur_date, "value": cur_val, "baseline": med, "mad": mad,
        "z": z, "pct_change": pct, "history_n": len(history),
        "history": [(str(d), v) for d, v in history],
    }


def _severity(z: float, mon: dict) -> str | None:
    direction = (mon.get("direction") or "both").lower()
    if direction == "down" and z > 0:
        return None
    if direction == "up" and z < 0:
        return None
    az = abs(z)
    if az >= float(mon.get("threshold_high") or 3.0):
        return "high"
    if az >= float(mon.get("threshold_med") or 2.0):
        return "medium"
    return None


# ---------- diễn giải bằng LLM ----------

def _fmt_num(v: float, unit: str) -> str:
    if unit.upper() in ("VND", "đ", "VNĐ"):
        return f"{v:,.0f}đ"
    if unit == "%":
        return f"{v:,.2f}%"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"


async def _narrate(mon: dict, ev: dict) -> str:
    """Gọi agent viết 3-5 câu: chuyện gì → có thể vì sao → nên làm gì."""
    from . import agent

    unit = mon.get("unit") or ""
    hist = "; ".join(f"{d}: {_fmt_num(v, unit)}" for d, v in ev["history"])
    prompt = (
        f"[CẢNH BÁO SỐ LIỆU BẤT THƯỜNG — {mon['name']}]\n"
        f"Hệ thống giám sát đã phát hiện (bằng thống kê, không cần bạn xác nhận lại):\n"
        f"- Chỉ số: {mon.get('metric_label') or mon['name']}\n"
        f"- Ngày {ev['date']}: {_fmt_num(ev['value'], unit)}\n"
        f"- Mức thường thấy cùng thứ trong tuần: {_fmt_num(ev['baseline'], unit)}"
        + (f" (lệch {ev['pct_change']:+.1f}%)" if ev.get("pct_change") is not None else "")
        + f"\n- Độ lệch chuẩn hóa: z = {ev['z']:+.1f}\n"
        f"- Các mốc so sánh: {hist}\n\n"
        "Nhiệm vụ: viết 3-5 câu tiếng Việt cho Ban điều hành:\n"
        "1) Chuyện gì đang xảy ra (nêu số).\n"
        "2) Có thể vì sao — được phép chạy thêm bq_query để bóc tách theo brand/kênh/"
        "danh mục tìm nguyên nhân (dùng bq_recent_queries để lấy lại query cũ cho đúng bảng).\n"
        "3) Đề xuất 1 việc nên làm ngay.\n"
        "Nếu bóc tách thấy nguyên nhân kỹ thuật (ETL trễ, thiếu dữ liệu) thì nói thẳng.\n"
        "Ngắn gọn, không lặp lại số liệu thừa, KHÔNG chào hỏi. Kết bằng 1 khuyến nghị."
    )
    system_prompt = agent.build_system_prompt(mon["name"], True, channel="lark")
    reply = await agent.run(prompt, system_prompt)
    return (reply.text or "").strip()


# ---------- kiểm tra 1 monitor ----------

def _cooldown_ok(mon: dict, now: dt.datetime) -> bool:
    last = mon.get("last_alert_at")
    if not last:
        return True
    try:
        last_dt = dt.datetime.fromisoformat(str(last).replace("Z", "+00:00")).astimezone(VN)
    except Exception:
        return True
    return (now - last_dt).total_seconds() >= float(mon.get("cooldown_hours") or 12) * 3600


def _check_signpost(mon: dict) -> dict | None:
    """Signpost: SQL trả 1 giá trị, so với threshold_value theo direction."""
    rows = _bq_rows(mon["sql"], max_rows=5)
    if not rows:
        return None
    v = rows[0].get("v")
    if v is None or mon.get("threshold_value") is None:
        return None
    v, thr = float(v), float(mon["threshold_value"])
    direction = (mon.get("direction") or "down").lower()
    hit = v <= thr if direction == "down" else v >= thr
    if not hit:
        return None
    return {"date": dt.datetime.now(VN).date(), "value": v, "baseline": thr,
            "z": 0.0, "pct_change": ((v - thr) / thr * 100) if thr else None,
            "history_n": 0, "history": [], "signpost": True}


async def check_monitor(mon: dict, defaults: dict | None = None) -> dict | None:
    """Chạy 1 monitor. Trả event đã ghi DB nếu có bất thường, ngược lại None."""
    defaults = defaults or _defaults()
    now = dt.datetime.now(VN)
    sb = db.sb()
    sb.table("monitors").update(
        {"last_checked_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    ).eq("id", mon["id"]).execute()

    is_signpost = (mon.get("kind") or "anomaly") == "signpost"
    if is_signpost:
        ev = _check_signpost(mon)
        severity = "high" if ev else None
    else:
        rows = _bq_rows(mon["sql"])
        ev = evaluate(rows, mon.get("baseline_method") or "weekday_median",
                      defaults["min_history"], defaults["noise_floor_pct"])
        if not ev:
            log.info("Monitor '%s': chưa đủ dữ liệu baseline", mon["name"])
            return None
        severity = _severity(ev["z"], mon)

    if not ev or not severity:
        return None
    if _in_blackout(ev["date"], defaults):
        log.info("Monitor '%s': %s là ngày blackout — bỏ qua", mon["name"], ev["date"])
        return None
    if not _cooldown_ok(mon, now):
        log.info("Monitor '%s': còn trong cooldown — bỏ qua", mon["name"])
        return None

    # Phân tầng: cao → nhắn ngay (kể cả giờ im lặng) · trung bình → digest gom ·
    # thấp → chỉ ghi log.
    send_now = severity == "high"

    narrative = ""
    if defaults["narrative"] and severity in ("high", "medium"):
        try:
            narrative = await _narrate(mon, ev)
        except Exception:
            log.exception("Diễn giải bất thường lỗi — vẫn gửi cảnh báo số liệu thô")

    row = sb.table("anomaly_events").insert({
        "monitor_id": mon["id"], "monitor_name": mon["name"],
        "value": ev["value"], "baseline": ev["baseline"],
        "z_score": round(ev["z"], 2),
        "pct_change": round(ev["pct_change"], 2) if ev.get("pct_change") is not None else None,
        "severity": severity, "narrative": narrative,
        "notified": send_now, "chat_id": mon.get("chat_id"),
    }).execute().data[0]

    if send_now:
        _send_alert(mon, ev, severity, narrative)
        sb.table("monitors").update(
            {"last_alert_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        ).eq("id", mon["id"]).execute()
    log.info("Monitor '%s': %s (z=%.1f) — %s", mon["name"], severity, ev["z"],
             "đã gửi" if send_now else "ghi log chờ digest")
    return row


def _send_alert(mon: dict, ev: dict, severity: str, narrative: str) -> None:
    from .scheduler import _send_result

    unit = mon.get("unit") or ""
    icon = "🔴" if severity == "high" else "🟠"
    kind_label = "Ngưỡng kế hoạch đã chạm" if (mon.get("kind") == "signpost") \
        else "Bất thường số liệu"
    head = (f"{icon} {kind_label}: {mon.get('metric_label') or mon['name']}\n"
            f"Ngày {ev['date']}: {_fmt_num(ev['value'], unit)} "
            f"(thường thấy {_fmt_num(ev['baseline'], unit)}"
            + (f", lệch {ev['pct_change']:+.1f}%" if ev.get("pct_change") is not None else "")
            + ")")
    text = head + (f"\n\n{narrative}" if narrative else "")
    _send_result(mon.get("channel") or "lark", mon["chat_id"], text)


# ---------- vòng lặp (gọi từ scheduler) ----------

def _due(mon: dict, now: dt.datetime) -> bool:
    from croniter import croniter

    base_str = mon.get("last_checked_at") or mon.get("created_at")
    if not base_str:
        return True
    try:
        base = dt.datetime.fromisoformat(str(base_str).replace("Z", "+00:00")).astimezone(VN)
        return croniter(mon.get("check_cron") or "0 9,15,21 * * *",
                        base).get_next(dt.datetime) <= now
    except Exception:
        log.error("check_cron không hợp lệ ở monitor '%s'", mon.get("name"))
        return False


def maybe_check() -> None:
    """Chạy các monitor đến hạn. Gọi mỗi vòng scheduler — tự gate theo check_cron."""
    import asyncio

    defaults = _defaults()
    if not defaults["enabled"]:
        return
    try:
        mons = (db.sb().table("monitors").select("*")
                .eq("enabled", True).execute()).data
    except Exception as e:
        log.warning("Không đọc được bảng monitors: %s", e)
        return

    now = dt.datetime.now(VN)
    for mon in mons:
        if not mon.get("chat_id") or not _due(mon, now):
            continue
        try:
            asyncio.run(check_monitor(mon, defaults))
        except Exception:
            log.exception("Monitor '%s' chạy lỗi", mon.get("name"))


def recent_events(days: int = 7, only_unnotified: bool = False) -> list[dict]:
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    q = (db.sb().table("anomaly_events").select("*")
         .gte("created_at", since).order("created_at", desc=True).limit(50))
    if only_unnotified:
        q = q.eq("notified", False)
    return q.execute().data
