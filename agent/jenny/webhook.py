"""Webhook server — nhận meeting notes từ Circleback & Lark Meeting Agent.

Chạy sau Caddy (HTTPS). URL chứa secret trong path:
  POST /webhook/circleback/<WEBHOOK_SECRET>     ← Circleback automation
  POST /webhook/lark-meeting/<WEBHOOK_SECRET>   ← Lark Anycross / thủ công
  GET  /health
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from . import db, lark_memory as memory

log = logging.getLogger(__name__)

app = FastAPI(title="Jenny webhooks", docs_url=None, redoc_url=None)

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


def _check(secret: str) -> None:
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=404)


def _get(payload: dict, *keys: str, default: str = "") -> Any:
    for k in keys:
        for src in (payload, payload.get("meeting", {}) or {}):
            if isinstance(src, dict) and src.get(k):
                return src[k]
    return default


def _format_circleback(p: dict) -> tuple[str, str, str]:
    """Trả về (title, markdown) — chịu được nhiều biến thể payload."""
    title = str(_get(p, "name", "title", "meetingName", default="Cuộc họp không tên"))
    day = str(_get(p, "date", "startTime", "start_time", "createdAt",
                   default=memory.today_vn()))[:10]

    parts = [f"# {title}", "", f"- Ngày: {day}", "- Nguồn: Circleback"]
    attendees = _get(p, "attendees", "participants", default=[])
    if attendees:
        names = [a.get("name") or a.get("email", "?") if isinstance(a, dict) else str(a)
                 for a in attendees]
        parts.append(f"- Tham dự: {', '.join(names)}")
    url = _get(p, "url", "meetingUrl", "recordingUrl")
    if url:
        parts.append(f"- Link: {url}")
    parts.append("")

    handled = {"name", "title", "meetingName", "date", "startTime", "start_time",
               "createdAt", "attendees", "participants", "url", "meetingUrl",
               "recordingUrl", "meeting"}

    for key, heading in (("summary", "Tóm tắt"), ("overview", "Tóm tắt"),
                         ("notes", "Ghi chú"), ("outline", "Nội dung"),
                         ("transcript", "Transcript")):
        val = _get(p, key)
        if val:
            handled.add(key)
            parts += [f"## {heading}", val if isinstance(val, str)
                      else json.dumps(val, ensure_ascii=False, indent=2), ""]

    actions = _get(p, "actionItems", "action_items", "tasks", default=[])
    if actions:
        handled.update({"actionItems", "action_items", "tasks"})
        parts.append("## Action items")
        for a in actions:
            if isinstance(a, dict):
                who = a.get("assignee") or a.get("owner") or ""
                parts.append(f"- {a.get('title') or a.get('text') or a} "
                             + (f"(@{who})" if who else ""))
            else:
                parts.append(f"- {a}")
        parts.append("")

    rest = {k: v for k, v in p.items() if k not in handled and v}
    if rest:
        parts += ["## Dữ liệu khác", "```json",
                  json.dumps(rest, ensure_ascii=False, indent=2)[:4000], "```"]
    return title, day, "\n".join(parts)


def _save_meeting(source: str, title: str, day: str, md: str) -> dict:
    fname = f"{day}-{memory.slugify(title)}.md"
    res = memory.save_markdown("meetings", fname, md)
    token = res.get("file_token", "")
    memory.append_index(f"- [meetings/{fname}] (token:{token}) · {day} · {title} ({source})")
    db.log_tool_call(None, None, f"meeting_ingest_{source}",
                     {"title": title, "file": fname},
                     result_summary=res.get("location"))
    log.info("Meeting saved (%s): %s → %s", source, fname, res["status"])
    return {"ok": True, "file": fname, **res}


@app.get("/health")
def health() -> dict:
    from . import lark_user
    try:
        lark_user.access_token()
        lark_ok = True
    except Exception:
        lark_ok = False
    return {"ok": True, "memory": memory.ready(), "lark_user": lark_ok}


# ---------- OAuth cho account Lark của Jenny ----------

def _redirect_uri(request: Request) -> str:
    return str(request.base_url).rstrip("/").replace("http://", "https://") \
        + "/lark/oauth/callback"


@app.get("/lark/oauth/start/{secret}")
def lark_oauth_start(secret: str, request: Request):
    from fastapi.responses import RedirectResponse
    from . import lark_user
    _check(secret)
    return RedirectResponse(lark_user.authorize_url(_redirect_uri(request), secret))


@app.get("/lark/oauth/callback")
def lark_oauth_callback(code: str = "", state: str = "", request: Request = None):
    from fastapi.responses import HTMLResponse
    from . import lark_user
    _check(state)
    if not code:
        raise HTTPException(status_code=422, detail="Thiếu code")
    tok = lark_user.exchange_code(code, _redirect_uri(request))
    return HTMLResponse(
        f"<h3>✅ Jenny đã đăng nhập Lark thành công</h3>"
        f"<p>Account: <b>{tok.get('name')}</b> ({tok.get('open_id')})</p>"
        f"<p>Đóng tab này được rồi ạ.</p>")


@app.post("/webhook/circleback/{secret}")
async def circleback(secret: str, request: Request) -> dict:
    _check(secret)
    payload = await request.json()
    title, day, md = _format_circleback(payload)
    return _save_meeting("circleback", title, day, md)


@app.post("/webhook/lark-meeting/{secret}")
async def lark_meeting(secret: str, request: Request) -> dict:
    """Nhận {"doc_url" | "doc_token", "title"?} → đọc doc bằng account Jenny → lưu .md."""
    from . import lark_user
    _check(secret)
    payload = await request.json()
    ref = payload.get("doc_token") or payload.get("doc_url") or ""
    if not ref:
        raise HTTPException(status_code=422, detail="Thiếu doc_token/doc_url")
    try:
        content = lark_user.read_document(ref)
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f"Đọc doc thất bại: {e} "
                                   "(account Jenny cần được cấp quyền xem doc)")
    title = payload.get("title") or (content.splitlines()[0].strip("# ") if content
                                     else "Lark meeting")
    day = payload.get("date") or memory.today_vn()
    md = f"# {title}\n\n- Ngày: {day}\n- Nguồn: Lark Meeting Agent\n\n{content}"
    return _save_meeting("lark", title, day, md)
