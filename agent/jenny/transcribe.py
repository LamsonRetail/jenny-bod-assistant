"""Gỡ băng ghi âm qua Whisper Transcription Server nội bộ (large-v3, GPU).

Server API: POST /transcribe (multipart) → job_id → poll GET /result/{job_id}.
Base URL nằm ở config `transcribe_server` (Supabase) — ngrok đổi URL thì sửa
trên dashboard, không cần deploy.
"""
from __future__ import annotations

import logging
import time

import httpx

from . import db

log = logging.getLogger(__name__)

POLL_INTERVAL = 15          # giây giữa các lần poll kết quả
MAX_WAIT_MIN = 90           # chờ tối đa (họp dài + hàng đợi)

_HEADERS = {"ngrok-skip-browser-warning": "true"}  # bỏ trang chặn của ngrok free


def _base_url() -> str:
    cfg = db.all_configs().get("transcribe_server", {})
    url = (cfg.get("base_url") or "").rstrip("/")
    if not url:
        raise RuntimeError("Chưa cấu hình transcribe_server.base_url (config trên dashboard)")
    return url


def server_health() -> dict:
    r = httpx.get(f"{_base_url()}/health", headers=_HEADERS, timeout=15)
    return r.json()


def transcribe_audio(data: bytes, mime: str, title: str = "meeting") -> str:
    """Gửi audio lên Whisper server, chờ xử lý xong, trả về transcript."""
    base = _base_url()
    ext = {"audio/mp4": ".m4a", "audio/mpeg": ".mp3", "audio/wav": ".wav",
           "audio/aac": ".aac", "audio/ogg": ".ogg", "audio/flac": ".flac",
           "video/mp4": ".mp4", "video/webm": ".webm",
           "video/quicktime": ".mov"}.get(mime, ".m4a")
    log.info("Whisper submit: %.1f MB (%s) — %s", len(data) / 1e6, mime, title)

    r = httpx.post(f"{base}/transcribe",
                   params={"language": "vi", "task": "transcribe"},
                   data={"meeting_title": title},
                   files={"file": (f"meeting{ext}", data, mime)},
                   headers=_HEADERS, timeout=600)
    job = r.json()
    job_id = job.get("job_id")
    if not job_id:
        raise RuntimeError(f"Whisper server từ chối: {r.status_code} {r.text[:200]}")
    log.info("Whisper job %s — queue %s, ETA %s phút",
             job_id, job.get("queue_position"), job.get("eta_minutes"))

    deadline = time.time() + MAX_WAIT_MIN * 60
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            res = httpx.get(f"{base}/result/{job_id}", headers=_HEADERS, timeout=30).json()
        except Exception as e:
            log.warning("Poll lỗi (thử tiếp): %s", e)
            continue
        status = res.get("status")
        if status == "done":
            transcript = (res.get("transcript") or "").strip()
            if not transcript:
                raise RuntimeError("Whisper trả transcript rỗng")
            log.info("Whisper xong: %d ký tự, xử lý %.0fs (model %s)",
                     len(transcript), res.get("processing_time_seconds") or 0,
                     res.get("model"))
            return transcript
        if status in ("failed", "error") or res.get("error"):
            raise RuntimeError(f"Whisper lỗi: {res.get('error') or status}")
    raise RuntimeError(f"Whisper quá {MAX_WAIT_MIN} phút chưa xong (job {job_id})")


MIME_BY_EXT = {
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".flac": "audio/flac", ".mp4": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime",
}


def mime_for(filename: str) -> str | None:
    from pathlib import Path
    return MIME_BY_EXT.get(Path(filename.lower()).suffix)


def to_wav(data: bytes, src_ext: str = ".opus") -> bytes:
    """Chuyển audio sang wav 16kHz mono bằng ffmpeg.

    Tin nhắn thoại Lark ở dạng opus — Whisper server nhận wav ổn định hơn.
    """
    import os
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=src_ext, delete=False) as f:
        f.write(data)
        src = f.name
    dst = src + ".wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                        "-ar", "16000", "-ac", "1", dst],
                       check=True, timeout=180)
        with open(dst, "rb") as f:
            return f.read()
    finally:
        for p in (src, dst):
            if os.path.exists(p):
                os.unlink(p)
