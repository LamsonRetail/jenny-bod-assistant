"""Đọc báo cáo thành giọng nói — brief sáng nghe được trên đường đi làm.

Đây là cách khả thi duy nhất để "tích hợp xe hơi": CarPlay/Android Auto không cho
trợ lý bên thứ ba vào, nhưng phát file audio thì luôn được.

Bản text và bản đọc KHÁC NHAU: bản đọc không có bảng/gạch đầu dòng/markdown, số
làm tròn, câu ngắn. Vì vậy phải viết lại script trước khi đọc, không TTS thẳng.
"""
from __future__ import annotations

import logging
import tempfile

import httpx

from . import config, db

log = logging.getLogger(__name__)

DEFAULT_VOICE = "pNInz6obpgDQGcFmaJgB"   # giọng đa ngữ mặc định của ElevenLabs
DEFAULT_MODEL = "eleven_multilingual_v2"


def _cfg() -> dict:
    cfg = db.all_configs().get("tts", {}) or {}
    return {
        "enabled": cfg.get("enabled", True),
        "voice_id": cfg.get("voice_id") or DEFAULT_VOICE,
        "model_id": cfg.get("model_id") or DEFAULT_MODEL,
        "max_chars": int(cfg.get("max_chars", 4000)),
        "speed": cfg.get("speed"),
    }


def available() -> bool:
    return bool(config.ELEVENLABS_API_KEY) and _cfg()["enabled"]


def audio_schedule_ids() -> list[str]:
    """config `audio_brief.schedule_ids` — những lịch nào cần đọc thành tiếng."""
    cfg = db.all_configs().get("audio_brief", {}) or {}
    return [str(i) for i in (cfg.get("schedule_ids") or [])]


async def make_script(text: str, title: str = "Brief") -> str:
    """Viết lại bản báo cáo thành script để NGHE (không phải để đọc bằng mắt)."""
    from . import agent

    prompt = (
        "Viết lại bản báo cáo dưới đây thành SCRIPT ĐỂ ĐỌC THÀNH TIẾNG cho lãnh đạo "
        "nghe trên đường đi làm.\n\n"
        "Yêu cầu:\n"
        "- Bỏ hết markdown, bảng, gạch đầu dòng, emoji, link. Chỉ văn xuôi.\n"
        "- Số làm tròn cho dễ nghe (vd '2 tỷ 6' thay vì '2.603.412.000 đồng').\n"
        "- Câu ngắn, mỗi câu một ý. Có mở đầu ngắn, thân bài, và kết bằng việc "
        "cần lưu ý nhất hôm nay.\n"
        "- Độ dài khoảng 2-3 phút đọc (300-450 chữ).\n"
        "- Giữ nguyên các con số quan trọng, không bịa thêm.\n"
        "- CHỈ trả về script, không giải thích gì thêm.\n\n"
        f"--- BÁO CÁO GỐC ---\n{text}")
    reply = await agent.run(prompt, agent.build_system_prompt(title, False, channel="lark"))
    return (reply.text or "").strip() or text


def speak(text: str, out_path: str | None = None) -> str:
    """Gọi ElevenLabs, ghi file mp3, trả về đường dẫn."""
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("Chưa có ELEVENLABS_API_KEY trong agent.env")
    cfg = _cfg()
    body: dict = {"text": text[:cfg["max_chars"]], "model_id": cfg["model_id"]}
    if cfg.get("speed"):
        body["voice_settings"] = {"speed": float(cfg["speed"])}

    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['voice_id']}",
        headers={"xi-api-key": config.ELEVENLABS_API_KEY,
                 "Content-Type": "application/json"},
        json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs lỗi {r.status_code}: {r.text[:200]}")

    out = out_path or tempfile.mktemp(suffix=".mp3", prefix="jenny-brief-")
    with open(out, "wb") as f:
        f.write(r.content)
    log.info("TTS xong: %.1f KB → %s", len(r.content) / 1024, out)
    return out


async def send_audio_version(channel: str, chat_id: str, text: str,
                             title: str = "Brief") -> bool:
    """Viết script → đọc → gửi file vào chat. Trả False nếu không làm được."""
    import os

    if not available() or channel != "lark":
        return False
    path = None
    try:
        script = await make_script(text, title)
        path = speak(script)
        from . import lark_user
        # chat_id có thể ở dạng "oc_xxx#om_yyy" (trả vào thread) — file gửi vào chat gốc
        lark_user.send_file(chat_id.split("#", 1)[0], path)
        return True
    except Exception:
        log.exception("Gửi bản audio lỗi — vẫn còn bản chữ")
        return False
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
