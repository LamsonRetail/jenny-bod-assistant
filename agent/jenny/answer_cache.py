"""Bộ nhớ câu trả lời — bảo đảm câu hỏi tương tự cho kết quả GIỐNG NHAU.

LLM vốn không tất định: hỏi lại cùng một câu có thể ra chữ khác, thậm chí số khác.
Yêu cầu vận hành là "câu hỏi tương tự nhau kết quả trả lời phải giống nhau", nên
cách chắc chắn nhất là: chuẩn hoá câu hỏi → nếu đã trả lời trong hạn TTL thì DÙNG
LẠI ĐÚNG NGUYÊN VĂN, không gọi LLM.

Tách cache theo `tier` (user | admin): câu trả lời cho admin (có thể chứa nhân sự /
tài chính) KHÔNG BAO GIỜ được phục vụ lại cho người thường.

Ghi chú TTL: cache càng dài càng nhất quán nhưng số liệu càng dễ cũ. Mặc định 12h
(cấu hình `answer_cache.ttl_hours` trong bảng configs).
"""

from __future__ import annotations

import logging
import re

from . import db
from .policy import strip_accents

log = logging.getLogger(__name__)

# Từ đệm không mang nội dung — bỏ đi để "cho anh xem doanh thu hôm nay với" và
# "doanh thu hôm nay" ra cùng một khoá.
_FILLER = {"jenny", "em", "anh", "chi", "chị", "oi", "ơi", "voi", "với", "nhe", "nhé",
           "a", "ạ", "cho", "toi", "tôi", "minh", "mình", "xem", "giup", "giúp", "lam",
           "làm", "the", "thế", "nao", "nào", "vay", "vậy", "hoi", "hỏi", "biet", "biết",
           "duoc", "được", "khong", "không", "co", "có", "la", "là", "va", "và", "thi",
           "thì", "de", "để", "ve", "về", "cua", "của", "may", "mấy", "bao", "nhieu",
           "nhiêu", "hay", "chut", "chút", "ok", "hello", "hi", "xin", "vui", "long", "lòng"}


def _cfg() -> dict:
    try:
        v = db.all_configs().get("answer_cache")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def enabled() -> bool:
    return bool(_cfg().get("enabled", True))


def normalize(text: str) -> str:
    """Chuẩn hoá câu hỏi: bỏ dấu, bỏ ký tự lạ, bỏ từ đệm, sắp xếp từ khoá còn lại.

    Sắp xếp để "doanh thu hôm nay bao nhiêu" và "hôm nay doanh thu bao nhiêu"
    trùng khoá — đúng tinh thần "câu hỏi tương tự".
    """
    s = strip_accents((text or "").lower())
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    words = [w for w in s.split() if w and w not in _FILLER and len(w) > 1]
    return " ".join(sorted(words))


def lookup(question: str, tier: str) -> dict | None:
    """Đã có câu trả lời cho câu tương tự (cùng tier, còn hạn)? → dict hoặc None."""
    if not enabled():
        return None
    key = normalize(question)
    if len(key) < 3:
        return None
    cfg = _cfg()
    try:
        res = db.sb().rpc("find_cached_answer", {
            "p_norm": key, "p_tier": tier,
            "p_ttl_hours": int(cfg.get("ttl_hours", 12)),
            "p_min_sim": float(cfg.get("min_similarity", 0.86)),
        }).execute()
        rows = res.data or []
        if not rows:
            return None
        hit = rows[0]
        db.sb().table("answer_cache").update({
            "hits": (hit.get("hits") or 0) + 1, "last_hit_at": "now()",
        }).eq("id", hit["id"]).execute()
        log.info("answer_cache HIT (sim=%.2f, tier=%s)", hit.get("sim") or 1.0, tier)
        return hit
    except Exception:
        # Cache là tối ưu hoá, không phải đường sống — lỗi thì cứ hỏi LLM.
        log.warning("answer_cache lookup lỗi — bỏ qua cache", exc_info=True)
        return None


def store(question: str, tier: str, answer: str) -> None:
    if not (enabled() and answer):
        return
    key = normalize(question)
    if len(key) < 3:
        return
    try:
        db.sb().table("answer_cache").upsert({
            "norm_key": key, "tier": tier,
            "question": question[:2000], "answer": answer[:20000],
        }, on_conflict="norm_key,tier").execute()
    except Exception:
        log.warning("answer_cache store lỗi — bỏ qua", exc_info=True)
