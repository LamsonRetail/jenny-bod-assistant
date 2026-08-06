"""Theo dõi comment/mention trong Docs, Base, Wiki — Jenny trả lời ngay trong comment.

Cách hoạt động: quét comment của các tài liệu Jenny biết (đã index trong bảng
`resources` + tài liệu Jenny tạo) mỗi CHECK_INTERVAL giây; comment mới có tag
Jenny (hoặc gọi tên) → agent xử lý → reply thẳng vào comment đó.

Cursor lưu ở config `doc_comment_cursor` (không cần bảng mới).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from . import agent, db, lark_user

log = logging.getLogger(__name__)

CHECK_INTERVAL = 120        # giây
MAX_DOCS = 40               # số tài liệu quét mỗi vòng (mới nhất trước)

_last_check = 0.0


def _cursor() -> dict:
    return dict(db.all_configs().get("doc_comment_cursor", {}))


def _save_cursor(cur: dict) -> None:
    db.sb().table("configs").upsert({
        "key": "doc_comment_cursor", "value": cur,
        "description": "Comment đã xử lý trong Docs/Base/Wiki (Jenny tự quản lý)",
    }, on_conflict="key").execute()


def _targets() -> list[dict]:
    """Tài liệu cần quét: {token, file_type, title, url}."""
    out, seen = [], set()
    cfgs = db.all_configs()

    mem = cfgs.get("lark_memory", {})
    if mem.get("index_doc"):
        out.append({"token": mem["index_doc"], "file_type": "docx",
                    "title": "INDEX kho bộ nhớ", "url": ""})
        seen.add(mem["index_doc"])

    dd = cfgs.get("bq_data_dictionary", {})
    if dd.get("url"):
        try:
            tok = _resolve(dd["url"])
            if tok and tok[0] not in seen:
                out.append({"token": tok[0], "file_type": tok[1],
                            "title": "Data dictionary", "url": dd["url"]})
                seen.add(tok[0])
        except Exception:
            pass

    try:
        rows = (db.sb().table("resources").select("url,title")
                .eq("kind", "lark_doc").order("created_at", desc=True)
                .limit(MAX_DOCS).execute()).data
    except Exception:
        rows = []
    for r in rows:
        try:
            tok = _resolve(r["url"])
        except Exception:
            continue
        if tok and tok[0] not in seen:
            out.append({"token": tok[0], "file_type": tok[1],
                        "title": r.get("title") or "", "url": r["url"]})
            seen.add(tok[0])
    return out


def _resolve(url: str) -> tuple[str, str] | None:
    """URL → (file_token, file_type) cho Drive comment API."""
    m = re.search(r"/(wiki|docx|docs|base|sheets)/([A-Za-z0-9]+)", url)
    if not m:
        return None
    kind, token = m.group(1), m.group(2)
    ftype = {"docx": "docx", "docs": "docx", "base": "bitable",
             "sheets": "sheet"}.get(kind, "")
    if kind == "wiki":
        node = lark_user._get("/wiki/v2/spaces/get_node", {"token": token}).get("node", {})
        token = node.get("obj_token", token)
        ftype = {"docx": "docx", "doc": "docx", "bitable": "bitable",
                 "sheet": "sheet"}.get(node.get("obj_type", "docx"), "docx")
    return (token, ftype or "docx")


def _list_comments(token: str, file_type: str) -> list[dict]:
    data = lark_user._get(f"/drive/v1/files/{token}/comments",
                          {"file_type": file_type, "page_size": 50,
                           "user_id_type": "open_id"})
    return data.get("items", [])


def _reply(token: str, file_type: str, comment_id: str, text: str) -> None:
    lark_user._post(f"/drive/v1/files/{token}/comments/{comment_id}/replies",
                    {"content": {"elements": [
                        {"type": "text_run", "text_run": {"text": text[:8000]}}]}},
                    params={"file_type": file_type, "user_id_type": "open_id"})


def _mentions_jenny(text: str, my_open_id: str, raw: dict) -> bool:
    if my_open_id and my_open_id in str(raw):
        return True
    triggers = db.all_configs().get("reply_rules", {}).get("trigger_names", ["jenny"])
    low = text.lower()
    return any(str(t).lower() in low for t in triggers)


def _reply_text(reply: dict) -> str:
    parts = []
    for el in (reply.get("content", {}) or {}).get("elements", []) or []:
        if el.get("type") == "text_run":
            parts.append(el.get("text_run", {}).get("text", ""))
        elif el.get("type") == "person":
            parts.append("@" + str(el.get("person", {}).get("user_id", "")))
        elif el.get("type") == "docs_link":
            parts.append(el.get("docs_link", {}).get("url", ""))
    return "".join(parts).strip()


def maybe_watch() -> None:
    global _last_check
    if time.time() - _last_check < CHECK_INTERVAL:
        return
    _last_check = time.time()
    try:
        _watch()
    except Exception:
        log.exception("Quét comment tài liệu lỗi")
    try:
        _watch_task_comments()
    except Exception:
        log.exception("Quét comment task lỗi")


def watch_doc_url(url: str, title: str = "") -> None:
    """Thêm 1 tài liệu vào danh sách theo dõi comment (ghi vào resources)."""
    db.save_resource({"channel": "lark", "kind": "lark_doc", "url": url,
                      "title": title or "", "chat_name": "(theo dõi comment)",
                      "context_note": "Jenny được yêu cầu theo dõi comment"})


def _watch_task_comments() -> None:
    """Comment trong task của việc BOD giao (task do Jenny tạo → đọc được)."""
    from . import assignments, org
    cur = _cursor()
    changed = False
    for a in assignments.list_active():
        guid = a.get("lark_task_guid") or ""
        if not guid:
            continue
        try:
            comments = lark_user.list_task_comments(guid)
        except Exception as e:
            log.debug("Không đọc được comment task %s: %s", guid, e)
            continue
        key = f"task:{guid}"
        for c in comments:
            cid = str(c.get("id") or "")
            creator = (c.get("creator") or {})
            if not cid or cid in cur.get(key, []):
                continue
            cur.setdefault(key, []).append(cid)
            cur[key] = cur[key][-100:]
            changed = True
            if creator.get("type") == "app":
                continue  # comment của chính Jenny
            text = (c.get("content") or "").strip()
            if not text:
                continue
            author = creator.get("id", "")
            person = org.get_person(author) or {}
            log.info("Comment mới trong task '%s' từ %s: %s",
                     a["title"], person.get("name") or author, text[:80])
            try:
                _answer_task_comment(a, text, author, person, guid)
            except Exception:
                log.exception("Trả lời comment task lỗi")
    if changed:
        _save_cursor(cur)


def _answer_task_comment(assignment: dict, text: str, author: str,
                         person: dict, guid: str) -> None:
    sp = agent.build_system_prompt(assignment["title"], True, channel="Lark Task")
    prompt = (
        f"Bạn nhận được comment trong task của việc BOD giao:\n"
        f"- Việc: {assignment['title']}\n"
        f"- Bối cảnh: {assignment.get('context')}\n"
        f"- Đầu ra mong đợi: {assignment.get('expected_outcome')}\n"
        f"- PIC: {assignment.get('pic_name')} · assignment_id: {assignment['id']}\n"
        f"- Người comment: {person.get('name') or author} "
        f"({person.get('job_title') or ''})\n"
        f"- Nội dung comment: {text}\n\n"
        "Xử lý theo skill bod-delegation (nếu là nộp kết quả thì đánh giá theo đầu ra "
        "mong đợi, thiếu thì nêu rõ cần bổ sung gì, đủ thì tổng hợp gửi BOD bằng "
        "assignment_notify_assigner + assignment_update). Sau đó viết PHẢN HỒI NGẮN "
        "(dưới 600 ký tự) để đăng lại vào comment của task. Chỉ trả về nội dung phản hồi."
    )
    reply = asyncio.run(agent.run(prompt, sp))
    lark_user.add_task_comment(guid, reply.text)
    log.info("Đã comment trả lời trong task '%s'", assignment["title"])


def _watch() -> None:
    me = lark_user.me().get("open_id", "")
    cur = _cursor()
    changed = False

    for doc in _targets():
        token, ftype = doc["token"], doc["file_type"]
        try:
            comments = _list_comments(token, ftype)
        except Exception as e:
            log.debug("Không đọc được comment %s: %s", token, e)
            continue

        for c in comments:
            for rep in (c.get("reply_list", {}) or {}).get("replies", []) or []:
                rid = str(rep.get("reply_id") or "")
                if not rid or rid in cur.get(token, []):
                    continue
                text = _reply_text(rep)
                author = rep.get("user_id", "")
                cur.setdefault(token, []).append(rid)
                cur[token] = cur[token][-200:]
                changed = True
                if author == me or not text:
                    continue
                if not _mentions_jenny(text, me, rep):
                    continue
                log.info("Comment tag Jenny trong '%s': %s", doc.get("title"), text[:80])
                try:
                    _answer(doc, c, text, author)
                except Exception:
                    log.exception("Trả lời comment lỗi")

    if changed:
        _save_cursor(cur)


def _answer(doc: dict, comment: dict, question: str, author_open_id: str) -> None:
    from . import org
    person = org.get_person(author_open_id) or {}
    who = person.get("name") or author_open_id
    ctx = f"{who} — {person.get('job_title') or ''} {person.get('department_path') or ''}".strip()

    quote = (comment.get("quote") or "").strip()
    sp = agent.build_system_prompt(doc.get("title"), True, channel="Lark Docs")
    prompt = (
        f"Bạn được tag trong một COMMENT trên tài liệu Lark “{doc.get('title')}”"
        + (f" ({doc.get('url')})" if doc.get("url") else "") + ".\n"
        f"Người hỏi: {ctx}\n"
        + (f"Đoạn văn bản được comment: “{quote[:500]}”\n" if quote else "")
        + f"Nội dung comment: {question}\n\n"
        "Trả lời NGẮN GỌN (dưới 800 ký tự, không markdown phức tạp vì hiển thị trong "
        "khung comment). Cần đọc nội dung tài liệu thì dùng read_lark_document với url "
        "của tài liệu. Chỉ đưa ra câu trả lời, không chào hỏi dài dòng."
    )
    reply = asyncio.run(agent.run(prompt, sp))
    _reply(doc["token"], doc["file_type"], str(comment.get("comment_id")), reply.text)
    log.info("Đã trả lời comment trong '%s'", doc.get("title"))
