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


def _platform_cfg() -> dict:
    """config `platform_a2a` — gọi agent khác qua API của LSR platform.

    Đây là đường ĐÚNG (không cần Jenny vào chat Lark của agent kia), nhưng hiện Caddy
    của platform chặn 403 mọi path ngoài /health, /v1/traces, /v1/policy/check. Khi
    platform mở endpoint A2A, chỉ cần khai config này là chạy — không phải sửa code:

      {"enabled": true,
       "url": "https://platform.…/v1/a2a/{agent}/ask",   # {agent} sẽ được thay
       "method": "POST",
       "auth": "bearer_telemetry",        # dùng LSR_TELEMETRY_API_KEY, hoặc "none"
       "headers": {},
       "body": {"question": "{question}", "from": "{from_agent}"},
       "answer_field": "answer",          # đường dẫn tới câu trả lời, vd "data.reply"
       "timeout_sec": 120}
    """
    return db.all_configs().get("platform_a2a", {}) or {}


def _dig(obj, path: str):
    cur = obj
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _ask_platform(peer: dict, question: str, cfg: dict) -> dict:
    """Hỏi agent khác qua API platform. Đồng bộ, trả cùng cấu trúc với _ask_lark."""
    import json as _json
    import os
    import urllib.error
    import urllib.request

    url = (cfg.get("url") or "").replace("{agent}", peer["name"])
    if not url:
        return {"status": "error", "agent": peer["name"],
                "error": "config `platform_a2a.url` chưa khai."}
    body = _json.loads(
        _json.dumps(cfg.get("body") or {"question": "{question}"})
        .replace("{question}", _json.dumps(question)[1:-1])
        .replace("{from_agent}", os.environ.get("LSR_AGENT_ID", "AG-JENNY-BOD")))
    headers = {"Content-Type": "application/json", **(cfg.get("headers") or {})}
    if (cfg.get("auth") or "bearer_telemetry") == "bearer_telemetry":
        key = os.environ.get("LSR_TELEMETRY_API_KEY", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(
        url, data=_json.dumps(body, ensure_ascii=False).encode(),
        headers=headers, method=(cfg.get("method") or "POST").upper())
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get("timeout_sec") or 120)) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", errors="replace")
        hint = ""
        if e.code == 403:
            hint = (" — platform đang chặn ở proxy (Caddy). Cần đội platform mở path A2A "
                    "cho agent token, hoặc khai lại `platform_a2a.auth`.")
        return {"status": "error", "agent": peer["name"],
                "error": f"platform trả {e.code}: {detail}{hint}"}
    except Exception as exc:
        return {"status": "error", "agent": peer["name"],
                "error": f"không gọi được platform: {exc}"}

    try:
        data = _json.loads(raw)
        answer = _dig(data, cfg.get("answer_field") or "answer") or raw
    except Exception:
        answer = raw
    answer = str(answer).strip()
    if not answer:
        return {"status": "timeout", "agent": peer["name"], "answer": "",
                "waited_sec": 0,
                "note": f"{peer['name']} trả về rỗng qua platform."}
    log.info("Agent %s trả lời qua platform (%d ký tự)", peer["name"], len(answer))
    return {"status": "answered", "agent": peer["name"], "answer": answer,
            "via": "platform", "waited_sec": 0}


def ask(name: str, question: str, wait_sec: int | None = None) -> dict:
    """Gửi câu hỏi cho 1 agent rồi chờ trả lời.

    Ưu tiên đi qua **API platform** (không cần Jenny ở trong chat Lark của agent kia);
    chưa khai/không gọi được thì mới quay về nhắn qua Lark.

    Trả về {"status": answered|timeout|error, "answer": str, "agent": str, ...}
    """
    from . import lark_user

    peer = get(name)
    if peer:
        pcfg = _platform_cfg()
        transport = (peer.get("transport") or "").lower()
        if pcfg.get("enabled") and transport != "lark_im":
            res = _ask_platform(peer, question, pcfg)
            if res["status"] == "answered":
                return res
            if transport == "platform":       # chỉ định rõ platform → không fallback
                return res
            log.warning("Hỏi %s qua platform không được (%s) — thử lại qua Lark",
                        peer["name"], res.get("error", "")[:120])
    if not peer:
        return {"status": "error",
                "error": f"Chưa có agent nào tên '{name}' trong config `peer_agents`."}
    chat_id = (peer.get("chat_id") or "").strip()
    if not chat_id:
        return {"status": "error", "agent": peer["name"],
                "error": (f"Không có đường nào để hỏi '{peer['name']}': API platform chưa "
                          "bật (config `platform_a2a.enabled`) và cũng chưa khai `chat_id` "
                          "Lark trong `peer_agents`. Cần một trong hai.")}

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
