"""Kho bộ nhớ .md của Jenny trên LARK DRIVE (account Jenny).

Cấu trúc: My Space của account Jenny
  Jenny-BOD-Memory/
  ├── INDEX (Lark doc — mỗi file 1 dòng, tra cứu 2 bước)
  ├── meetings/ reports/ market/ knowledge/ summaries/ (file .md upload)

Token các folder lưu ở config `lark_memory` — tự khởi tạo lần đầu.
Thiếu quyền thì fallback lưu /opt/jenny/pending-drive, có quyền chạy sync_pending().
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
from pathlib import Path

from . import db, lark_user

log = logging.getLogger(__name__)

ROOT_NAME = "Jenny-BOD-Memory"
SUBFOLDERS = ["meetings", "reports", "market", "knowledge", "summaries"]
PENDING_DIR = Path(os.environ.get("JENNY_PENDING_DIR", "/opt/jenny/pending-drive"))


def slugify(text: str, max_len: int = 60) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", text)[:max_len] or "untitled"


def today_vn() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).date().isoformat()


def _setup() -> dict:
    """Trả về {root, folders{name:token}, index_doc}; tự tạo lần đầu."""
    cfg = db.all_configs().get("lark_memory", {})
    if cfg.get("root") and cfg.get("index_doc"):
        return cfg

    root_meta = lark_user._get("/drive/explorer/v2/root_folder/meta")
    my_root = root_meta.get("token")
    root = lark_user._post("/drive/v1/files/create_folder",
                           {"name": ROOT_NAME, "folder_token": my_root}).get("token")
    folders = {}
    for name in SUBFOLDERS:
        folders[name] = lark_user._post("/drive/v1/files/create_folder",
                                        {"name": name, "folder_token": root}).get("token")
    doc = lark_user._post("/docx/v1/documents",
                          {"folder_token": root, "title": "INDEX"})
    index_doc = doc.get("document", {}).get("document_id")
    cfg = {"root": root, "folders": folders, "index_doc": index_doc}
    db.sb().table("configs").upsert({
        "key": "lark_memory", "value": cfg,
        "description": "Token kho bộ nhớ Lark Drive của Jenny (tự khởi tạo, đừng sửa tay)",
    }, on_conflict="key").execute()
    append_index("Danh mục file bộ nhớ Jenny — mỗi dòng: [thư-mục/tên-file] · ngày · tóm tắt. "
                 "Tra cứu 2 bước: đọc INDEX trước, chọn đúng file rồi mới đọc file.")
    log.info("Đã khởi tạo kho Lark Drive: root=%s", root)
    return cfg


def _upload(folder_token: str, filename: str, content: bytes) -> str:
    import httpx
    r = httpx.post(
        f"{lark_user.BASE}/drive/v1/files/upload_all",
        headers={"Authorization": f"Bearer {lark_user.access_token()}"},
        data={"file_name": filename, "parent_type": "explorer",
              "parent_node": folder_token, "size": str(len(content))},
        files={"file": (filename, content)},
        timeout=60,
    ).json()
    if r.get("code") != 0:
        raise RuntimeError(f"Upload Lark Drive lỗi: {r.get('code')} {r.get('msg')}")
    return r["data"]["file_token"]


def ready() -> bool:
    try:
        _setup()
        return True
    except Exception:
        return False


def save_markdown(subfolder: str, filename: str, content: str) -> dict:
    try:
        cfg = _setup()
        token = _upload(cfg["folders"][subfolder], filename, content.encode("utf-8"))
        try:  # đồng bộ sang NotebookLM (nền, best-effort)
            from . import nblm
            nblm.push_markdown(f"{subfolder}/{filename}", content)
        except Exception:
            pass
        return {"status": "uploaded", "location": f"lark-drive:{subfolder}/{filename}",
                "file_token": token}
    except Exception as e:
        PENDING_DIR.joinpath(subfolder).mkdir(parents=True, exist_ok=True)
        path = PENDING_DIR / subfolder / filename
        path.write_text(content, encoding="utf-8")
        log.warning("Lark Drive chưa sẵn sàng (%s) — lưu tạm %s", e, path)
        return {"status": "pending_local", "location": str(path)}


def append_index(line: str) -> bool:
    try:
        cfg = _setup()
        doc = cfg["index_doc"]
        lark_user._post(f"/docx/v1/documents/{doc}/blocks/{doc}/children", {
            "children": [{"block_type": 2,
                          "text": {"elements": [{"text_run": {"content": line.strip()}}]}}],
        })
        return True
    except Exception as e:
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        with open(PENDING_DIR / "INDEX.pending.md", "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
        log.warning("Chưa ghi được INDEX (%s) — lưu pending", e)
        return False


def sync_pending() -> int:
    if not PENDING_DIR.exists() or not ready():
        return 0
    n = 0
    for path in sorted(PENDING_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "INDEX.pending.md":
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    append_index(line)
            path.unlink()
            continue
        res = save_markdown(path.parent.name, path.name,
                            path.read_text(encoding="utf-8"))
        if res["status"] == "uploaded":
            path.unlink()
            n += 1
    return n
