"""Google Drive helper — ghi file .md vào kho Jenny-BOD-Memory.

Chưa có service account (SETUP.md mục D) thì fallback lưu vào
/opt/jenny/pending-drive/ — khi có credentials, chạy sync_pending() đẩy lên.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
from pathlib import Path

from . import db

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]
PENDING_DIR = Path(os.environ.get("JENNY_PENDING_DIR", "/opt/jenny/pending-drive"))

_service = None
_folder_cache: dict[str, str] = {}


def _drive():
    global _service
    if _service is not None:
        return _service
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds_path or not Path(creds_path).exists():
        return None
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def _root_config() -> dict:
    return db.all_configs().get("drive_memory_folder", {})


def _subfolder_id(name: str) -> str | None:
    if name in _folder_cache:
        return _folder_cache[name]
    svc, root = _drive(), _root_config().get("folder_id")
    if not svc or not root:
        return None
    res = svc.files().list(
        q=f"name = '{name}' and '{root}' in parents and trashed = false "
          "and mimeType = 'application/vnd.google-apps.folder'",
        fields="files(id)").execute()
    files = res.get("files", [])
    if not files:  # chưa có thì tạo
        created = svc.files().create(body={
            "name": name, "parents": [root],
            "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
        files = [created]
    _folder_cache[name] = files[0]["id"]
    return _folder_cache[name]


def slugify(text: str, max_len: int = 60) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", text)[:max_len] or "untitled"


def save_markdown(subfolder: str, filename: str, content: str) -> dict:
    """Ghi .md vào Drive; chưa có credentials thì lưu pending trên VPS."""
    from googleapiclient.http import MediaInMemoryUpload  # import muộn, tránh lỗi khi thiếu lib

    svc = _drive()
    if svc is None:
        PENDING_DIR.joinpath(subfolder).mkdir(parents=True, exist_ok=True)
        path = PENDING_DIR / subfolder / filename
        path.write_text(content, encoding="utf-8")
        log.warning("Chưa có Google credentials — lưu tạm %s", path)
        return {"status": "pending_local", "location": str(path)}

    parent = _subfolder_id(subfolder)
    media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/markdown")
    f = svc.files().create(
        body={"name": filename, "parents": [parent]},
        media_body=media, fields="id, webViewLink").execute()
    return {"status": "uploaded", "location": f.get("webViewLink"), "file_id": f["id"]}


def append_index(line: str) -> bool:
    """Thêm 1 dòng vào INDEX.md trên Drive (quy tắc bộ nhớ 2 bước)."""
    from googleapiclient.http import MediaInMemoryUpload

    svc = _drive()
    index_id = _root_config().get("index_file_id")
    if svc is None or not index_id:
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        with open(PENDING_DIR / "INDEX.pending.md", "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
        return False
    content = svc.files().get_media(fileId=index_id).execute().decode("utf-8")
    content = content.rstrip() + "\n" + line.rstrip() + "\n"
    svc.files().update(fileId=index_id,
                       media_body=MediaInMemoryUpload(content.encode("utf-8"),
                                                      mimetype="text/markdown")).execute()
    return True


def sync_pending() -> int:
    """Đẩy các file lưu tạm lên Drive sau khi có credentials."""
    if _drive() is None or not PENDING_DIR.exists():
        return 0
    n = 0
    for path in sorted(PENDING_DIR.rglob("*.md")):
        if path.name == "INDEX.pending.md":
            for line in path.read_text(encoding="utf-8").splitlines():
                append_index(line)
            path.unlink()
            continue
        save_markdown(path.parent.name, path.name, path.read_text(encoding="utf-8"))
        path.unlink()
        n += 1
    return n


def today_vn() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).date().isoformat()
