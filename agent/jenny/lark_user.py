"""Lark qua TÀI KHOẢN người dùng (user_access_token, OAuth).

Không dùng bot: Jenny đăng nhập bằng một account Lark thật, đọc/gửi tin nhắn
và đọc wiki/docs với đúng quyền của account đó. Token lưu ở Supabase config
`lark_user_token`, tự refresh trước khi hết hạn.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from . import config, db

log = logging.getLogger(__name__)

BASE = config.LARK_DOMAIN + "/open-apis"
ACCOUNTS = config.LARK_DOMAIN.replace("https://open.", "https://accounts.")

# Các scope user-token cần bật trong Developer Console (Permissions) + release
SCOPES = ("offline_access "
          "contact:user.base:readonly "
          "im:chat:readonly im:message im:message:readonly "
          "docx:document docx:document:readonly "
          "wiki:wiki:readonly "
          "drive:drive drive:drive:readonly "
          "bitable:app:readonly sheets:spreadsheet:readonly "
          "calendar:calendar calendar:calendar:readonly "
          "task:task task:task:readonly "
          "contact:contact:readonly contact:contact.base:readonly "
          "contact:department.base:readonly contact:user.employee:readonly "
          "im:resource "
          "minutes:minutes:readonly minutes:minute:download "
          "minutes:minutes.media:export")
# Tên scope minutes lấy từ thông báo lỗi 99991679 của chính Lark API
# (bản số ít "minutes:minute:readonly" không tồn tại → OAuth 20043).

_http = httpx.Client(timeout=30)


# ---------- Token store (Supabase config `lark_user_token`) ----------

def _load_token() -> dict:
    return db.all_configs().get("lark_user_token", {})


def _save_token(tok: dict) -> None:
    db.sb().table("configs").upsert({
        "key": "lark_user_token",
        "value": tok,
        "description": "OAuth token của account Lark Jenny (tự refresh, đừng sửa tay)",
    }, on_conflict="key").execute()


def authorize_url(redirect_uri: str, state: str) -> str:
    # OAuth v2 với scope tường minh — yêu cầu redirect_uri là domain thật
    # (Lark chặn domain chứa IP như nip.io → 403).
    from urllib.parse import urlencode
    return f"{ACCOUNTS}/open-apis/authen/v1/authorize?" + urlencode({
        "client_id": config.LARK_APP_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    })


def _app_access_token() -> str:
    r = _http.post(f"{BASE}/auth/v3/app_access_token/internal", json={
        "app_id": config.LARK_APP_ID, "app_secret": config.LARK_APP_SECRET,
    }).json()
    if not r.get("app_access_token"):
        raise RuntimeError(f"Không lấy được app_access_token: {r}")
    return r["app_access_token"]


def exchange_code(code: str, redirect_uri: str) -> dict:
    # Thử endpoint OAuth v2 trước, fallback OIDC v1 (dùng cho authorize legacy).
    r = _http.post(f"{BASE}/authen/v2/oauth/token", json={
        "grant_type": "authorization_code",
        "client_id": config.LARK_APP_ID,
        "client_secret": config.LARK_APP_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
    }).json()
    if not r.get("access_token"):
        oidc = _http.post(f"{BASE}/authen/v1/oidc/access_token",
                          headers={"Authorization": f"Bearer {_app_access_token()}"},
                          json={"grant_type": "authorization_code", "code": code}).json()
        if oidc.get("code") != 0:
            raise RuntimeError(f"OAuth đổi code thất bại: v2={r} oidc={oidc}")
        r = oidc["data"]
    tok = {
        "access_token": r["access_token"],
        "refresh_token": r.get("refresh_token", ""),
        "expires_at": time.time() + r.get("expires_in", 7200),
        "granted_scopes": r.get("scope", ""),
    }
    me = _http.get(f"{BASE}/authen/v1/user_info",
                   headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
    data = me.get("data", {})
    tok["open_id"] = data.get("open_id", "")
    tok["name"] = data.get("name", "")
    _save_token(tok)
    return tok


def access_token() -> str:
    """Trả access token còn hạn; tự refresh khi sắp hết."""
    tok = _load_token()
    if not tok.get("access_token"):
        raise RuntimeError("Chưa đăng nhập OAuth — mở /lark/oauth/start/<secret>")
    if time.time() < tok.get("expires_at", 0) - 300:
        return tok["access_token"]
    r = _http.post(f"{BASE}/authen/v2/oauth/token", json={
        "grant_type": "refresh_token",
        "client_id": config.LARK_APP_ID,
        "client_secret": config.LARK_APP_SECRET,
        "refresh_token": tok.get("refresh_token", ""),
    }).json()
    if not r.get("access_token"):
        oidc = _http.post(f"{BASE}/authen/v1/oidc/refresh_access_token",
                          headers={"Authorization": f"Bearer {_app_access_token()}"},
                          json={"grant_type": "refresh_token",
                                "refresh_token": tok.get("refresh_token", "")}).json()
        if oidc.get("code") != 0:
            raise RuntimeError(f"Refresh token thất bại (cần OAuth lại): v2={r} oidc={oidc}")
        r = oidc["data"]
    tok.update({
        "access_token": r["access_token"],
        "refresh_token": r.get("refresh_token", tok.get("refresh_token")),
        "expires_at": time.time() + r.get("expires_in", 7200),
    })
    _save_token(tok)
    return tok["access_token"]


def me() -> dict:
    tok = _load_token()
    return {"open_id": tok.get("open_id", ""), "name": tok.get("name", "")}


# ---------- API helpers (user token) ----------

def _get(path: str, params: dict | None = None) -> dict:
    r = _http.get(f"{BASE}{path}", params=params,
                  headers={"Authorization": f"Bearer {access_token()}"}).json()
    if r.get("code") != 0:
        raise RuntimeError(f"Lark GET {path}: {r.get('code')} {r.get('msg')}")
    return r.get("data", {})


def _post(path: str, body: dict, params: dict | None = None) -> dict:
    r = _http.post(f"{BASE}{path}", json=body, params=params,
                   headers={"Authorization": f"Bearer {access_token()}"}).json()
    if r.get("code") != 0:
        raise RuntimeError(f"Lark POST {path}: {r.get('code')} {r.get('msg')}")
    return r.get("data", {})


def list_chats() -> list[dict]:
    """Các chat mà account Jenny đang tham gia."""
    items, page_token = [], ""
    while True:
        data = _get("/im/v1/chats", {"page_size": 100, "page_token": page_token})
        items += data.get("items", [])
        page_token = data.get("page_token", "")
        if not data.get("has_more"):
            return items


def list_messages(chat_id: str, start_time_sec: int) -> list[dict]:
    data = _get("/im/v1/messages", {
        "container_id_type": "chat", "container_id": chat_id,
        "start_time": start_time_sec, "sort_type": "ByCreateTimeAsc",
        "page_size": 50,
    })
    return data.get("items", [])


def send_text_to_user(open_id: str, text: str) -> str:
    """Nhắn riêng theo open_id (tự mở p2p chat) — trả về chat_id của chat riêng."""
    data = _post("/im/v1/messages", {
        "receive_id": open_id, "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }, params={"receive_id_type": "open_id"})
    return data.get("chat_id", "")


def send_text(chat_id: str, text: str) -> list[str]:
    """Gửi text (tự cắt khúc). Trả về danh sách message_id đã gửi."""
    ids = []
    for i in range(0, len(text), 9000):
        data = _post("/im/v1/messages", {
            "receive_id": chat_id, "msg_type": "text",
            "content": json.dumps({"text": text[i:i + 9000]}, ensure_ascii=False),
        }, params={"receive_id_type": "chat_id"})
        ids.append(data.get("message_id", ""))
    return ids


def update_text(message_id: str, text: str) -> None:
    """Sửa nội dung 1 tin nhắn text đã gửi (dùng cho placeholder '⏳ đang xử lý')."""
    r = _http.put(f"{BASE}/im/v1/messages/{message_id}",
                  json={"msg_type": "text",
                        "content": json.dumps({"text": text[:9000]}, ensure_ascii=False)},
                  headers={"Authorization": f"Bearer {access_token()}"}).json()
    if r.get("code") != 0:
        raise RuntimeError(f"Lark PUT message: {r.get('code')} {r.get('msg')}")


def _flatten_field(v: Any) -> str:
    if isinstance(v, list):
        return " ".join(_flatten_field(x) for x in v)
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or v.get("link") or v)
    return str(v)


def _read_bitable(app_token: str) -> str:
    """Đọc toàn bộ Bitable (Lark Base) thành markdown."""
    tables = _get(f"/bitable/v1/apps/{app_token}/tables",
                  {"page_size": 100}).get("items", [])
    parts = []
    for t in tables:
        parts.append(f"## Bảng: {t.get('name')}")
        records, page_token = [], ""
        while True:
            params: dict = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            data = _get(f"/bitable/v1/apps/{app_token}/tables/{t['table_id']}/records",
                        params)
            records += data.get("items", [])
            page_token = data.get("page_token", "")
            if not data.get("has_more"):
                break
        for rec in records:
            fields = rec.get("fields", {})
            line = " · ".join(f"{k}: {_flatten_field(v)}" for k, v in fields.items() if v)
            parts.append(f"- {line}")
        if not records:
            parts.append("(trống)")
        parts.append("")
    return "\n".join(parts)


def _patch(path: str, body: dict, params: dict | None = None) -> dict:
    r = _http.patch(f"{BASE}{path}", json=body, params=params,
                    headers={"Authorization": f"Bearer {access_token()}"}).json()
    if r.get("code") != 0:
        raise RuntimeError(f"Lark PATCH {path}: {r.get('code')} {r.get('msg')}")
    return r.get("data", {})


def _delete(path: str) -> None:
    r = _http.delete(f"{BASE}{path}",
                     headers={"Authorization": f"Bearer {access_token()}"}).json()
    if r.get("code") != 0:
        raise RuntimeError(f"Lark DELETE {path}: {r.get('code')} {r.get('msg')}")


def download_message_resource(message_id: str, file_key: str) -> tuple[bytes, str]:
    """Tải file đính kèm trong tin nhắn — trả về (bytes, content_type)."""
    r = _http.get(f"{BASE}/im/v1/messages/{message_id}/resources/{file_key}",
                  params={"type": "file"},
                  headers={"Authorization": f"Bearer {access_token()}"},
                  follow_redirects=True, timeout=300)
    ct = r.headers.get("content-type", "")
    if ct.startswith("application/json"):
        data = r.json()
        raise RuntimeError(f"Tải file tin nhắn lỗi: {data.get('code')} {data.get('msg')}")
    return r.content, ct


def vc_recording(meeting_id: str) -> dict:
    """URL bản ghi cuộc họp VC (tenant token) — dùng khi nhận event recording_ready."""
    r = _http.get(f"{BASE}/vc/v1/meetings/{meeting_id}/recording",
                  headers={"Authorization": f"Bearer {_tenant_token()}"}).json()
    if r.get("code") != 0:
        raise RuntimeError(f"VC recording {meeting_id}: {r.get('code')} {r.get('msg')}")
    return r.get("data", {})


def download_url(url: str) -> tuple[bytes, str]:
    """Tải file từ URL bản ghi (kèm tenant token nếu là API của Lark)."""
    headers = {}
    if "larksuite.com" in url or "feishu" in url:
        headers["Authorization"] = f"Bearer {_tenant_token()}"
    r = _http.get(url, headers=headers, follow_redirects=True, timeout=900)
    return r.content, r.headers.get("content-type", "audio/mp4").split(";")[0]


def minutes_meta(url_or_token: str) -> dict:
    """Thông tin bản ghi Minutes (tenant token)."""
    import re
    m = re.search(r"/minutes/([A-Za-z0-9]+)", url_or_token)
    token = m.group(1) if m else url_or_token
    return _minutes_get(token, "").get("minute", {})


def _minutes_get(token: str, tail: str) -> dict:
    """Minutes API — CHỈ dùng tenant token."""
    r = _http.get(f"{BASE}/minutes/v1/minutes/{token}{tail}",
                  headers={"Authorization": f"Bearer {_tenant_token()}"}).json()
    if r.get("code") == 0:
        return r.get("data", {})
    if r.get("code") == 2091005:
        raise RuntimeError(
            "Bot Jenny chưa có quyền xem bản ghi này — anh/chị bấm Share trên "
            "Minutes và thêm Jenny (hoặc bật 'người trong tổ chức có link đều xem được'), "
            "rồi gửi lại link cho em ạ.")
    raise RuntimeError(f"Lark minutes: {r.get('code')} {r.get('msg')}")


def download_minutes_media(url_or_token: str) -> tuple[bytes, str]:
    """Tải audio/video từ Lark Minutes (tenant token)."""
    import re
    m = re.search(r"/minutes/([A-Za-z0-9]+)", url_or_token)
    token = m.group(1) if m else url_or_token
    data = _minutes_get(token, "/media")
    dl = data.get("download_url", "")
    if not dl:
        raise RuntimeError(f"Minutes không có download_url: {data}")
    r = _http.get(dl, follow_redirects=True, timeout=600)
    return r.content, r.headers.get("content-type", "audio/mp4").split(";")[0]


MAX_IM_FILE = 28 * 1024 * 1024  # Lark chặn file tin nhắn ~30MB


def send_file(chat_id: str, path: str) -> None:
    """Upload file rồi gửi vào chat. Audio quá 28MB tự nén bằng ffmpeg."""
    import os as _os
    import subprocess
    from pathlib import Path as _P

    if _os.path.getsize(path) > MAX_IM_FILE and _P(path).suffix.lower() in (
            ".m4a", ".mp3", ".wav", ".ogg", ".aac", ".flac"):
        small = str(_P(path).with_suffix("")) + "-nen.m4a"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                        "-ac", "1", "-b:a", "40k", small], check=True, timeout=600)
        path = small

    name = _P(path).name
    with open(path, "rb") as f:
        r = _http.post(f"{BASE}/im/v1/files",
                       data={"file_type": "stream", "file_name": name},
                       files={"file": (name, f)},
                       headers={"Authorization": f"Bearer {access_token()}"},
                       timeout=300).json()
    if r.get("code") != 0:
        raise RuntimeError(f"Upload file Lark lỗi: {r.get('code')} {r.get('msg')}")
    file_key = r["data"]["file_key"]
    _post("/im/v1/messages", {
        "receive_id": chat_id, "msg_type": "file",
        "content": json.dumps({"file_key": file_key}),
    }, params={"receive_id_type": "chat_id"})


def download_file(file_token: str) -> bytes:
    r = _http.get(f"{BASE}/drive/v1/files/{file_token}/download",
                  headers={"Authorization": f"Bearer {access_token()}"},
                  follow_redirects=True)
    if r.headers.get("content-type", "").startswith("application/json"):
        data = r.json()
        raise RuntimeError(f"Download lỗi: {data.get('code')} {data.get('msg')}")
    return r.content


# ---------- Contacts (cơ cấu tổ chức) ----------

def list_all_departments() -> list[dict]:
    """Toàn bộ phòng ban (đệ quy từ gốc)."""
    items, page_token = [], ""
    while True:
        data = _get("/contact/v3/departments/0/children",
                    {"fetch_child": "true", "page_size": 50, "page_token": page_token,
                     "department_id_type": "open_department_id",
                     "user_id_type": "open_id"})
        items += data.get("items", [])
        page_token = data.get("page_token", "")
        if not data.get("has_more"):
            return items


def users_in_department(open_department_id: str) -> list[dict]:
    items, page_token = [], ""
    while True:
        data = _get("/contact/v3/users/find_by_department",
                    {"department_id": open_department_id, "page_size": 50,
                     "page_token": page_token,
                     "department_id_type": "open_department_id",
                     "user_id_type": "open_id"})
        items += data.get("items", [])
        page_token = data.get("page_token", "")
        if not data.get("has_more"):
            return items


def delete_drive_file(file_token: str) -> None:
    _delete(f"/drive/v1/files/{file_token}?type=file")


# ---------- Calendar (lịch của account Jenny + lịch được share) ----------

_primary_cal: str = ""


def primary_calendar_id() -> str:
    global _primary_cal
    if _primary_cal:
        return _primary_cal
    data = _post("/calendar/v4/calendars/primary", {})
    cals = data.get("calendars", [])
    if cals:
        _primary_cal = cals[0].get("calendar", {}).get("calendar_id", "")
    if not _primary_cal:
        raise RuntimeError(f"Không lấy được primary calendar: {data}")
    return _primary_cal


def list_events(start_ts: int, end_ts: int) -> list[dict]:
    cal = primary_calendar_id()
    items, page_token = [], ""
    while True:
        data = _get(f"/calendar/v4/calendars/{cal}/events",
                    {"start_time": start_ts, "end_time": end_ts,
                     "page_size": 100, "page_token": page_token})
        items += data.get("items", [])
        page_token = data.get("page_token", "")
        if not data.get("has_more"):
            return items


def create_event(summary: str, start_ts: int, end_ts: int,
                 description: str = "") -> dict:
    cal = primary_calendar_id()
    return _post(f"/calendar/v4/calendars/{cal}/events", {
        "summary": summary, "description": description,
        "start_time": {"timestamp": str(start_ts)},
        "end_time": {"timestamp": str(end_ts)},
    }).get("event", {})


def delete_event(event_id: str) -> None:
    cal = primary_calendar_id()
    _delete(f"/calendar/v4/calendars/{cal}/events/{event_id}")


def reply_event(event_id: str, rsvp: str) -> None:
    """Trả lời lời mời họp: rsvp = 'accept' | 'decline' | 'tentative'.

    Họp định kỳ mà Jenny chỉ được add vào từng buổi (exception instance)
    có thể không reply được qua API — thử instance id rồi series id (_0).
    """
    import re
    cal = primary_calendar_id()
    candidates = [event_id]
    if re.search(r"_\d+$", event_id):
        candidates.append(re.sub(r"_\d+$", "_0", event_id))
    last = None
    for eid in candidates:
        try:
            _post(f"/calendar/v4/calendars/{cal}/events/{eid}/reply",
                  {"rsvp_status": rsvp})
            return
        except Exception as e:
            last = e
    raise last  # type: ignore[misc]


def event_attendees(event_id: str) -> list[dict]:
    cal = primary_calendar_id()
    items, page_token = [], ""
    while True:
        data = _get(f"/calendar/v4/calendars/{cal}/events/{event_id}/attendees",
                    {"page_size": 100, "page_token": page_token,
                     "user_id_type": "open_id"})
        items += data.get("items", [])
        page_token = data.get("page_token", "")
        if not data.get("has_more"):
            return items


# ---------- Task v2 ----------
# Lark chặn cấp scope task cho user token (legacy) → fallback tenant token của app:
# bot tạo task, gán người thật làm assignee — task vẫn hiện trong Lark Tasks của họ.

_tenant_tok: dict = {"token": "", "exp": 0.0}


def _tenant_token() -> str:
    if time.time() < _tenant_tok["exp"] - 60:
        return _tenant_tok["token"]
    r = _http.post(f"{BASE}/auth/v3/tenant_access_token/internal", json={
        "app_id": config.LARK_APP_ID, "app_secret": config.LARK_APP_SECRET}).json()
    if not r.get("tenant_access_token"):
        raise RuntimeError(f"Không lấy được tenant token: {r}")
    _tenant_tok.update({"token": r["tenant_access_token"],
                        "exp": time.time() + r.get("expire", 7200)})
    return _tenant_tok["token"]


def _task_request(method: str, path: str, body: dict | None = None,
                  params: dict | None = None) -> dict:
    """Task API — CHỈ dùng tenant token (Lark chặn scope task cho user token)."""
    r = _http.request(method, f"{BASE}{path}", json=body, params=params,
                      headers={"Authorization": f"Bearer {_tenant_token()}"}).json()
    if r.get("code") == 0:
        return r.get("data", {})
    if r.get("code") == 1470403:
        raise RuntimeError(
            "Bot Jenny chưa có quyền trên task này — người tạo task cần thêm "
            "Jenny làm follower/assignee, hoặc giao việc qua Jenny để Jenny tạo task.")
    raise RuntimeError(f"Lark {method} {path}: {r.get('code')} {r.get('msg')}")

def create_task(summary: str, due_ts_ms: int | None = None,
                description: str = "", assignee_open_id: str = "") -> dict:
    body: dict = {"summary": summary}
    if description:
        body["description"] = description
    if due_ts_ms:
        body["due"] = {"timestamp": str(due_ts_ms), "is_all_day": False}
    if assignee_open_id:
        body["members"] = [{"id": assignee_open_id, "type": "user", "role": "assignee"}]
    return _task_request("POST", "/task/v2/tasks", body,
                         params={"user_id_type": "open_id"}).get("task", {})


def list_tasks() -> list[dict]:
    items, page_token = [], ""
    while True:
        data = _task_request("GET", "/task/v2/tasks", None,
                             {"page_size": 100, "page_token": page_token,
                              "type": "my_tasks"})
        items += data.get("items", [])
        page_token = data.get("page_token", "")
        if not data.get("has_more") or not page_token:
            return items


def list_task_comments(task_guid: str) -> list[dict]:
    """Comment trong 1 task (task do Jenny/app tạo mới đọc được — Lark chặn user token)."""
    return _task_request("GET", "/task/v2/comments", None,
                         {"resource_type": "task", "resource_id": task_guid,
                          "page_size": 50, "user_id_type": "open_id"}).get("items", [])


def add_task_comment(task_guid: str, text: str) -> None:
    _task_request("POST", "/task/v2/comments",
                  {"content": text[:8000], "resource_type": "task",
                   "resource_id": task_guid},
                  {"user_id_type": "open_id"})


def complete_task(task_guid: str) -> None:
    import time as _t
    _task_request("PATCH", f"/task/v2/tasks/{task_guid}",
                  {"task": {"completed_at": str(int(_t.time() * 1000))},
                   "update_fields": ["completed_at"]})


def read_document(url_or_token: str) -> str:
    """Đọc nội dung wiki/doc/bitable bằng quyền của account Jenny.

    Nhận link wiki (…/wiki/<token>), link docx (…/docx/<token>),
    link base (…/base/<token>) hoặc token trần (coi như wiki node).
    """
    import re
    m = re.search(r"/(wiki|docx|docs|base)/([A-Za-z0-9]+)", url_or_token)
    kind, token = (m.group(1), m.group(2)) if m else ("wiki", url_or_token)
    obj_type = {"docx": "docx", "docs": "docx", "base": "bitable"}.get(kind, "")
    if kind == "wiki":
        node = _get("/wiki/v2/spaces/get_node", {"token": token}).get("node", {})
        token = node.get("obj_token", token)
        obj_type = node.get("obj_type", "docx")
    if obj_type == "bitable":
        return _read_bitable(token)
    if obj_type in ("docx", "doc"):
        return _get(f"/docx/v1/documents/{token}/raw_content").get("content", "")
    raise RuntimeError(f"Chưa hỗ trợ đọc loại tài liệu '{obj_type}'")
