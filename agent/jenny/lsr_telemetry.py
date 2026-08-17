"""Gửi telemetry của Jenny về LSR Agent Platform.

Vì sao có file này: trước đây trace chỉ đến từ hook `.claude/settings.json` khi chạy
Claude Code TRÊN MÁY DEV — mà `deploy.sh` lại loại trừ `.claude` khi rsync lên VPS,
nên Jenny production KHÔNG BAO GIỜ báo cáo (platform tưởng agent im lặng 65h dù
đang chạy). Nay Jenny tự gửi ngay trong `agent.run()`, không phụ thuộc file settings.

Nguyên tắc: chỉ dùng thư viện chuẩn, chạy ở thread nền, mọi lỗi bị chặn lại —
telemetry KHÔNG được phép làm hỏng hay làm chậm việc trả lời người dùng.

Env (đặt trong config/secrets/agent.env):
    LSR_COLLECTOR          https://collector.34-126-154-135.sslip.io
    LSR_AGENT_ID           AG-JENNY-BOD
    LSR_TELEMETRY_API_KEY  lsr_tel_...   (rỗng = tắt telemetry, Jenny vẫn chạy)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

COLLECTOR = os.environ.get("LSR_COLLECTOR", "https://collector.34-126-154-135.sslip.io").rstrip("/")
AGENT_ID = os.environ.get("LSR_AGENT_ID", "AG-JENNY-BOD")
API_KEY = os.environ.get("LSR_TELEMETRY_API_KEY", "")
TIMEOUT = float(os.environ.get("LSR_TELEMETRY_TIMEOUT", "5"))

enabled = bool(API_KEY)
if not enabled:
    log.info("LSR telemetry TẮT (thiếu LSR_TELEMETRY_API_KEY) — Jenny vẫn chạy bình thường")


def _post(trace: dict) -> None:
    req = urllib.request.Request(
        COLLECTOR + "/v1/traces",
        data=json.dumps(trace, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {API_KEY}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            r.read()
    except urllib.error.HTTPError as e:
        # 403 = agent bị deactivate trên platform; 401 = key sai. Log để còn biết,
        # nhưng KHÔNG raise (Jenny đã trả lời người dùng xong rồi).
        log.warning("LSR telemetry bị từ chối %s: %s", e.code, e.read()[:200])
    except Exception as exc:
        log.warning("LSR telemetry lỗi mạng: %s", exc)


def send(*, run_id: str | None, task_id: str | None = None, source: str = "production",
         usage: dict | None = None, model: str | None = None,
         tool_calls: list | None = None, final_output: str = "",
         duration_ms: int | None = None, error: str = "", extra: dict | None = None) -> None:
    """Đẩy 1 lượt xử lý về platform. Không chặn luồng gọi, không bao giờ raise."""
    if not enabled:
        return
    u = usage or {}
    trace = {
        "run_id": run_id,
        "agent_id": AGENT_ID,
        "task_id": task_id,
        "source": source,
        # collector cộng token từ llm_calls
        "llm_calls": [{
            "model": model,
            "input_tokens": int(u.get("input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0),
            "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
        }],
        "tool_calls": [{"name": (t or {}).get("name"), "ok": True}
                       for t in (tool_calls or [])],
        # collector tự che PII trước khi lưu; vẫn cắt ngắn để không đẩy cả bài dài.
        "final_output": (final_output or "")[:2000],
        "duration_ms": duration_ms,
        "status": "error" if error else "ok",
        "error": error[:500],
    }
    if extra:
        trace.update(extra)
    threading.Thread(target=_post, args=(trace,), daemon=True).start()
