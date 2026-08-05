"""NotebookLM (Gemini Notebook) — tích hợp qua thư viện notebooklm-py (unofficial).

Auth: đăng nhập 1 lần bằng `notebooklm login` (trên Mac), copy thư mục auth
sang VPS tại NBLM_AUTH_PATH. Notebook làm việc lưu ở config `notebooklm`.

Lưu ý: API không chính thức — Google đổi là có thể hỏng; mọi hàm đều
raise lỗi rõ ràng để tool/agent báo người dùng.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from . import db

log = logging.getLogger(__name__)

AUTH_PATH = os.environ.get("NBLM_AUTH_PATH", "/opt/jenny/notebooklm-auth")
NOTEBOOK_TITLE = "Jenny — LSR BOD Knowledge"

def _storage():
    """Context manager client — mở theo từng lần gọi (mỗi tool call 1 event loop)."""
    p = Path(AUTH_PATH)
    if p.is_dir() and (p / "storage_state.json").exists():
        p = p / "storage_state.json"
    if not p.exists():
        raise RuntimeError("Chưa đăng nhập NotebookLM (thiếu file auth trên VPS)")
    from notebooklm import NotebookLMClient
    return NotebookLMClient.from_storage(path=str(p))


async def _notebook_id(client) -> str:
    cfg = db.all_configs().get("notebooklm", {})
    nb_id = cfg.get("notebook_id", "")
    if nb_id:
        return nb_id
    nb = await client.notebooks.create(NOTEBOOK_TITLE)
    nb_id = getattr(nb, "id", None) or getattr(nb, "notebook_id", "")
    db.sb().table("configs").upsert({
        "key": "notebooklm",
        "value": {"notebook_id": nb_id, "title": NOTEBOOK_TITLE, "enabled": True},
        "description": "Notebook làm việc trên NotebookLM (Jenny tự tạo/quản lý)",
    }, on_conflict="key").execute()
    log.info("Đã tạo notebook '%s' (%s)", NOTEBOOK_TITLE, nb_id)
    return nb_id


def enabled() -> bool:
    cfg = db.all_configs().get("notebooklm", {})
    return bool(cfg.get("enabled", True)) and Path(AUTH_PATH).exists()


async def add_markdown_source(title: str, markdown: str) -> str:
    async with _storage() as client:
        return await _add_markdown(client, title, markdown)


async def _add_markdown(client, title: str, markdown: str) -> str:
    nb_id = await _notebook_id(client)
    src = await client.sources.add_text(nb_id, title, markdown, wait=True)
    return getattr(src, "id", "") or "ok"


async def add_url_source(url: str) -> str:
    async with _storage() as client:
        nb_id = await _notebook_id(client)
        src = await client.sources.add_url(nb_id, url)
        return getattr(src, "id", "") or "ok"


async def ask(question: str) -> str:
    async with _storage() as client:
        nb_id = await _notebook_id(client)
        result = await client.chat.ask(nb_id, question)
        for attr in ("answer", "text", "content"):
            val = getattr(result, attr, None)
            if isinstance(val, str) and val.strip():
                return val
        return str(result)


async def audio_overview(out_path: str, instructions: str = "") -> str:
    """Tạo Audio Overview (podcast) và tải về out_path. Mất vài phút."""
    async with _storage() as client:
        nb_id = await _notebook_id(client)
        await client.artifacts.generate_audio(
            nb_id, language="vi", instructions=instructions or None)
        last = None
        for _ in range(60):  # chờ tối đa ~20 phút
            await asyncio.sleep(20)
            try:
                await client.artifacts.download_audio(nb_id, out_path)
                return out_path
            except Exception as e:  # chưa sẵn sàng — chờ tiếp
                last = e
        raise RuntimeError(f"Audio quá lâu chưa xong: {last}")


# ---------- sync facades (gọi từ code sync như lark_memory) ----------

def push_markdown(title: str, markdown: str) -> None:
    """Đẩy 1 tài liệu vào notebook — best effort, chạy nền, không raise."""
    if not enabled():
        return

    def _run() -> None:
        try:
            asyncio.run(add_markdown_source(title, markdown))
            log.info("NotebookLM: đã thêm source '%s'", title)
        except Exception as e:
            log.warning("NotebookLM: không thêm được source '%s': %s", title, e)

    import threading
    threading.Thread(target=_run, daemon=True).start()
