"""Sổ đăng ký "ai tag được" trong từng chat — gồm cả BOT APP.

Vì sao cần: API thành viên của Lark (`im/v1/chats/:id/members`) chỉ nhận
member_id_type = user_id/union_id/open_id và **không trả về bot app**; endpoint
`/members/bot` thì 404. Nhưng bot app **vẫn có open_id dạng `ou_…` và vẫn tag được** —
id đó xuất hiện trong mảng `mentions` của những tin nhắn đã từng tag chúng.

Cách làm: học dần từ lịch sử tin nhắn (mentions) + bắt thụ động khi poller đọc tin mới.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time

from . import db

log = logging.getLogger(__name__)

_humans_cache: tuple[float, set] = (0.0, set())
HUMANS_TTL = 600


def _humans() -> set:
    """open_id của người thật (danh bạ Contacts) — để phân biệt bot."""
    global _humans_cache
    if time.time() - _humans_cache[0] < HUMANS_TTL and _humans_cache[1]:
        return _humans_cache[1]
    try:
        rows = db.sb().table("people").select("open_id").execute().data
        ids = {r["open_id"] for r in rows}
        _humans_cache = (time.time(), ids)
        return ids
    except Exception:
        return _humans_cache[1]


def _mention_id(men: dict) -> str:
    mid = men.get("id")
    if isinstance(mid, dict):
        return mid.get("open_id") or ""
    return mid or ""


_classified: dict[str, bool] = {}      # open_id → is_bot (cache trong tiến trình)


def classify_bots(open_ids: list[str]) -> dict[str, bool]:
    """open_id → có phải BOT không.

    Không thể chỉ dựa vào bảng `people`: org sync từ Contacts có thể chưa phủ hết nhân
    sự, người thật bị thiếu sẽ bị gắn nhãn bot oan. Nên với các id lạ phải hỏi thẳng
    Contact API — có trong danh bạ = người, không có = bot app.
    """
    from . import lark_user

    humans = _humans()
    out: dict[str, bool] = {}
    unknown = []
    for oid in open_ids:
        if oid in humans:
            out[oid] = False
        elif oid in _classified:
            out[oid] = _classified[oid]
        else:
            unknown.append(oid)
    if unknown:
        real = lark_user.users_batch_get(unknown)
        for oid in unknown:
            is_bot = oid not in real
            _classified[oid] = is_bot
            out[oid] = is_bot
    return out


def save_seen(chat_id: str, mentions: list | None) -> int:
    """Ghi nhận những ai được tag trong 1 tin nhắn. Trả về số dòng mới/cập nhật."""
    if not chat_id or not mentions:
        return 0
    chat_id = chat_id.split("#", 1)[0]
    ids = [_mention_id(m) for m in mentions]
    ids = [i for i in ids if i.startswith("ou_")]
    if not ids:
        return 0
    is_bot = classify_bots(ids)
    rows = []
    for men in mentions:
        oid = _mention_id(men)
        if not oid or not oid.startswith("ou_"):
            continue
        rows.append({
            "chat_id": chat_id, "open_id": oid,
            "name": (men.get("name") or "")[:200],
            "is_bot": is_bot.get(oid, False),
            "last_seen_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
    if not rows:
        return 0
    try:
        db.sb().table("chat_mentionables").upsert(
            rows, on_conflict="chat_id,open_id").execute()
        return len(rows)
    except Exception as e:
        log.debug("Không lưu được mentionables: %s", e)
        return 0


def discover(chat_id: str, days: int = 180, max_pages: int = 30) -> dict:
    """Quét lịch sử 1 chat, học mọi đối tượng từng được tag (kể cả bot)."""
    from . import lark_user

    chat_id = chat_id.split("#", 1)[0]
    since = int(time.time()) - days * 86400
    token, seen, msgs = "", {}, 0
    for _ in range(max_pages):
        params = {"container_id_type": "chat", "container_id": chat_id,
                  "start_time": since, "sort_type": "ByCreateTimeDesc",
                  "page_size": 50}
        if token:
            params["page_token"] = token
        try:
            data = lark_user._get("/im/v1/messages", params)
        except Exception as e:
            log.warning("Quét chat %s lỗi: %s", chat_id, e)
            break
        items = data.get("items", [])
        msgs += len(items)
        for m in items:
            for men in (m.get("mentions") or []):
                oid = _mention_id(men)
                # Quét từ MỚI đến CŨ; setdefault để giữ TÊN MỚI NHẤT (bot hay bị đổi tên)
                if oid.startswith("ou_"):
                    seen.setdefault(oid, men.get("name") or "")
        token = data.get("page_token") or ""
        if not data.get("has_more"):
            break

    saved = save_seen(chat_id, [{"id": o, "name": n} for o, n in seen.items()])
    is_bot = classify_bots(list(seen))
    bots = {o: n for o, n in seen.items() if is_bot.get(o)}
    log.info("Chat %s: đọc %d tin, học %d đối tượng tag được (%d bot)",
             chat_id, msgs, len(seen), len(bots))
    return {"messages": msgs, "total": len(seen), "bots": bots, "saved": saved}


def list_for_chat(chat_id: str, keyword: str = "",
                  bots_only: bool = False) -> list[dict]:
    chat_id = chat_id.split("#", 1)[0]
    try:
        q = (db.sb().table("chat_mentionables").select("*")
             .eq("chat_id", chat_id).order("name"))
        if bots_only:
            q = q.eq("is_bot", True)
        rows = q.execute().data
    except Exception as e:
        log.debug("Đọc mentionables lỗi: %s", e)
        return []
    kw = (keyword or "").strip().lower()
    if kw:
        rows = [r for r in rows if kw in (r.get("name") or "").lower()]
    return rows


# ---------- quét định kỳ (gọi từ scheduler) ----------

def maybe_discover() -> None:
    """Mỗi 24h quét lại các group để cập nhật danh sách tag được (bot mới thêm...)."""
    last = db.all_configs().get("mentionables_scan", {}).get("last", "")
    if last:
        try:
            if dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
                    last.replace("Z", "+00:00")) < dt.timedelta(hours=24):
                return
        except Exception:
            pass
    from . import lark_user
    total = {}
    try:
        chats = [c for c in lark_user.list_chats() if c.get("chat_type") != "p2p"]
    except Exception as e:
        log.warning("Không liệt kê được chat để quét: %s", e)
        return
    for c in chats:
        try:
            res = discover(c["chat_id"], days=90, max_pages=10)
            total[c.get("name") or c["chat_id"]] = len(res["bots"])
        except Exception:
            log.exception("Quét mentionables chat %s lỗi", c.get("name"))
    db.sb().table("configs").upsert({
        "key": "mentionables_scan",
        "value": {"last": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "bots_per_chat": total},
        "description": "Lần quét gần nhất danh sách tag được (gồm bot) trong các group",
    }, on_conflict="key").execute()
    log.info("Quét mentionables xong: %s", json.dumps(total, ensure_ascii=False))
