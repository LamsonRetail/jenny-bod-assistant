"""Scheduler — chạy scheduled_tasks (Supabase) theo cron, giờ Việt Nam.

Mỗi task: prompt giao cho agent → kết quả gửi vào chat đích (lark/telegram).
Quản lý task trên dashboard (thêm/sửa/bật/tắt) — service tự nhận, không cần restart.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx
from croniter import croniter

from . import agent, config, db

log = logging.getLogger(__name__)

VN = dt.timezone(dt.timedelta(hours=7))
CHECK_INTERVAL = 30  # giây


def _send_result(channel: str, chat_id: str, text: str) -> None:
    if channel == "lark":
        from . import lark_user
        # chat_id dạng "oc_xxx#om_yyy" → trả kết quả vào đúng thread chứa om_yyy
        if "#" in chat_id:
            _, reply_msg_id = chat_id.split("#", 1)
            for i in range(0, len(text), 9000):
                lark_user.reply_in_thread(reply_msg_id, text[i:i + 9000])
            return
        lark_user.send_text(chat_id, text)
    elif channel == "telegram":
        for i in range(0, len(text), 4096):
            httpx.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text[i:i + 4096]}, timeout=30)
    else:
        raise RuntimeError(f"Kênh không hỗ trợ: {channel}")


def _is_due(row: dict, now: dt.datetime) -> bool:
    base_str = row.get("last_run_at") or row.get("created_at")
    base = dt.datetime.fromisoformat(base_str.replace("Z", "+00:00")).astimezone(VN)
    try:
        next_run = croniter(row["cron"], base).get_next(dt.datetime)
    except Exception:
        log.error("Cron không hợp lệ ở task '%s': %s", row.get("name"), row.get("cron"))
        return False
    return next_run <= now


async def _run_task(row: dict) -> None:
    log.info("Chạy scheduled task: %s", row["name"])
    # đánh dấu trước để không chạy trùng nếu task kéo dài qua chu kỳ check
    db.sb().table("scheduled_tasks").update(
        {"last_run_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    ).eq("id", row["id"]).execute()

    system_prompt = agent.build_system_prompt(row.get("name"), True,
                                              channel=row.get("channel") or "lark")
    prompt = (f"[NHIỆM VỤ ĐỊNH KỲ — {row['name']}] Hôm nay là "
              f"{dt.datetime.now(VN).strftime('%A %d/%m/%Y %H:%M')} (giờ VN).\n\n"
              f"{row['prompt']}\n\n"
              "Kết quả trả về sẽ được gửi thẳng vào chat — viết hoàn chỉnh, "
              "không hỏi lại, không nói về quá trình thực hiện.")
    last_err = None
    for attempt in (1, 2):  # thử lại 1 lần sau 60s nếu lỗi
        try:
            reply = await agent.run(prompt, system_prompt)
            channel = row.get("channel") or "lark"
            _send_result(channel, row["chat_id"], reply.text)
            # Lịch được đánh dấu trong config `audio_brief` → gửi thêm bản đọc thành tiếng
            try:
                from . import tts
                if str(row["id"]) in tts.audio_schedule_ids():
                    await tts.send_audio_version(channel, row["chat_id"], reply.text,
                                                 row["name"])
            except Exception:
                log.exception("Bản audio lỗi — bản chữ đã gửi xong")
            db.log_tool_call(reply.session_id, None, "scheduled_task",
                             {"name": row["name"]}, result_summary="sent")
            log.info("Task '%s' xong (lần %d), đã gửi %s/%s",
                     row["name"], attempt, row.get("channel"), row["chat_id"])
            return
        except Exception as e:
            last_err = e
            log.exception("Task '%s' thất bại (lần %d)", row["name"], attempt)
            if attempt == 1:
                await asyncio.sleep(60)

    db.log_tool_call(None, None, "scheduled_task", {"name": row["name"]},
                     status="error", error=str(last_err)[:500])
    try:  # báo vào chat để không bị im lặng — gửi tin không cần Claude
        _send_result(row.get("channel") or "lark", row["chat_id"],
                     f"⚠️ Nhiệm vụ định kỳ '{row['name']}' chạy lỗi 2 lần. "
                     f"Lỗi: {str(last_err)[:200]}\n"
                     "Nếu lỗi nhắc đến đăng nhập/authenticate: cần đăng nhập lại "
                     "Claude trên VPS.")
    except Exception:
        log.exception("Không gửi được thông báo lỗi")


def _maybe_sync_org() -> None:
    """Đồng bộ cơ cấu tổ chức từ Lark Contacts mỗi 24h."""
    last = db.all_configs().get("org_sync", {}).get("last", "")
    if last:
        last_dt = dt.datetime.fromisoformat(last)
        if dt.datetime.now(VN) - last_dt < dt.timedelta(hours=24):
            return
    from . import org
    try:
        res = org.sync_org()
        log.info("Org sync: %s", res)
    except Exception as e:
        log.warning("Org sync chưa chạy được: %s", e)
        # đặt mốc lùi 23h để 1h nữa thử lại thay vì dồn dập
        db.sb().table("configs").upsert({
            "key": "org_sync",
            "value": {"last": (dt.datetime.now(VN) - dt.timedelta(hours=23)).isoformat(),
                      "error": str(e)[:300]},
            "description": "Trạng thái đồng bộ cơ cấu tổ chức (tự động hằng ngày)",
        }, on_conflict="key").execute()


def run_scheduler() -> None:
    log.info("Jenny scheduler: bắt đầu (check mỗi %ss, giờ VN)", CHECK_INTERVAL)
    import time
    while True:
        try:
            now = dt.datetime.now(VN)
            _maybe_sync_org()
            from . import anomaly, assignments, decisions, doc_watch, meetings
            meetings.maybe_watch()
            doc_watch.maybe_watch()
            anomaly.maybe_check()        # cảnh báo bất thường + signpost
            assignments.maybe_chase()    # đôn đốc việc đã giao
            decisions.maybe_review()     # đến hạn đo kết quả quyết định
            rows = (db.sb().table("scheduled_tasks").select("*")
                    .eq("enabled", True).execute()).data
            for row in rows:
                # Lịch có hạn dừng: quá hạn thì tự tắt, không chạy nữa
                exp = row.get("expires_at")
                if exp:
                    try:
                        exp_dt = dt.datetime.fromisoformat(
                            exp.replace("Z", "+00:00")).astimezone(VN)
                        if now > exp_dt:
                            db.sb().table("scheduled_tasks").update(
                                {"enabled": False}).eq("id", row["id"]).execute()
                            log.info("Lịch '%s' đã hết hạn (%s) → tắt", row["name"], exp)
                            continue
                    except Exception:
                        log.warning("expires_at không hợp lệ ở '%s': %s", row["name"], exp)
                if row.get("chat_id") and _is_due(row, now):
                    asyncio.run(_run_task(row))
        except Exception:
            log.exception("Vòng scheduler lỗi — tiếp tục")
        time.sleep(CHECK_INTERVAL)
