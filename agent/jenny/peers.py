"""Hỏi agent khác qua Lark rồi chờ trả lời (agent-to-agent qua chat).

Dùng khi việc đó do agent khác phụ trách — ví dụ biên bản họp do **Mino** làm, Jenny
không tự gỡ băng nữa mà đi hỏi Mino.

Cách hoạt động: gửi câu hỏi vào chat của agent đó (tag nếu là group) → poll tin mới
trong chat cho tới khi thấy tin KHÔNG phải của Jenny → trả về nội dung đó.

Danh bạ agent nằm ở config `peer_agents` — thêm/sửa trên dashboard, không cần deploy:
  {"mino": {"chat_id": "oc_…", "open_id": "ou_…", "role": "biên bản họp",
            "wait_sec": 120}}
"""
from __future__ import annotations

import json
import logging
import time

from . import db

log = logging.getLogger(__name__)

DEFAULT_WAIT = 120        # giây chờ agent kia trả lời
POLL_EVERY = 5


def registry() -> dict:
    return db.all_configs().get("peer_agents", {}) or {}


def get(name: str) -> dict | None:
    reg = registry()
    key = (name or "").strip().lower()
    if key in reg:
        return {"name": key, **reg[key]}
    for k, v in reg.items():                       # khớp một phần theo tên
        if key and (key in k.lower() or key in str(v.get("role", "")).lower()):
            return {"name": k, **v}
    return None


def _text_of(msg: dict) -> str:
    try:
        body = json.loads(msg.get("body", {}).get("content", "{}"))
    except Exception:
        return ""
    if isinstance(body, dict):
        return (body.get("text") or body.get("content") or "").strip()
    return ""


def ask(name: str, question: str, wait_sec: int | None = None) -> dict:
    """Gửi câu hỏi cho 1 agent rồi chờ trả lời.

    Trả về {"status": answered|timeout|error, "answer": str, "agent": str, ...}
    """
    from . import lark_user

    peer = get(name)
    if not peer:
        return {"status": "error",
                "error": f"Chưa có agent nào tên '{name}' trong config `peer_agents`."}
    chat_id = (peer.get("chat_id") or "").strip()
    if not chat_id:
        return {"status": "error",
                "error": f"Agent '{peer['name']}' chưa khai chat_id trong config."}

    wait = int(wait_sec or peer.get("wait_sec") or DEFAULT_WAIT)
    my_id = ""
    try:
        my_id = lark_user.me().get("open_id", "")
    except Exception:
        pass

    ats = [peer["open_id"]] if peer.get("open_id") else None
    sent_at = int(time.time())
    try:
        lark_user.send_text(chat_id, question, mention_open_ids=ats)
    except Exception as e:
        return {"status": "error", "agent": peer["name"],
                "error": f"Không gửi được câu hỏi cho {peer['name']}: {e}"}
    log.info("Đã hỏi agent %s (chat %s), chờ tối đa %ds", peer["name"], chat_id, wait)

    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(POLL_EVERY)
        try:
            msgs = lark_user.list_messages(chat_id, sent_at)
        except Exception as e:
            log.warning("Đọc chat %s lỗi: %s", chat_id, e)
            continue
        for m in msgs:
            sender = (m.get("sender") or {}).get("id") or ""
            if sender == my_id:                    # tin của chính Jenny
                continue
            txt = _text_of(m)
            if not txt:
                continue                           # card/ảnh — chờ tin có chữ
            log.info("Agent %s đã trả lời (%d ký tự)", peer["name"], len(txt))
            return {"status": "answered", "agent": peer["name"], "answer": txt,
                    "msg_type": m.get("msg_type"), "waited_sec": int(time.time() - sent_at)}

    return {"status": "timeout", "agent": peer["name"], "waited_sec": wait,
            "answer": "",
            "note": f"{peer['name']} chưa trả lời trong {wait}s. Có thể agent đang bận, "
                    "hết quota, hoặc câu hỏi cần người xử lý."}
