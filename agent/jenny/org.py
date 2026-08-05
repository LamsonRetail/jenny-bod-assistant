"""Cơ cấu tổ chức: đồng bộ từ Lark Contacts → bảng `people` + sơ đồ tổ chức .md.

- sync_org(): chạy hằng ngày (scheduler) hoặc thủ công — cập nhật people,
  sinh lại file `knowledge/so-do-to-chuc.md` trong kho bộ nhớ.
- get_person(open_id): hồ sơ 1 người (kèm ghi chú Jenny tự học).
- search_people(keyword): tìm theo tên/chức danh/phòng ban.
"""
from __future__ import annotations

import datetime as dt
import logging

from . import db, lark_user

log = logging.getLogger(__name__)


def _dept_paths(depts: list[dict]) -> dict[str, str]:
    """open_department_id → 'Khối A > Phòng B'."""
    by_id = {d["open_department_id"]: d for d in depts}
    paths: dict[str, str] = {}

    def path(did: str) -> str:
        if did in paths:
            return paths[did]
        d = by_id.get(did)
        if not d:
            return ""
        name = d.get("name") or f"(phòng ban {did[-6:]})"
        parent = d.get("parent_department_id", "")
        parent_path = path(parent) if parent in by_id else ""
        p = f"{parent_path} > {name}" if parent_path else name
        paths[did] = p
        return p

    for did in by_id:
        path(did)
    return paths


def sync_org() -> dict:
    depts = lark_user.list_all_departments()
    paths = _dept_paths(depts)

    people: dict[str, dict] = {}
    dept_members: dict[str, list[str]] = {}
    dept_ids = ["0"] + [d["open_department_id"] for d in depts]
    leader_ids = {d.get("leader_user_id") for d in depts if d.get("leader_user_id")}

    for did in dept_ids:
        try:
            users = lark_user.users_in_department(did)
        except Exception as e:
            log.warning("Không đọc được phòng ban %s: %s", did, e)
            continue
        for u in users:
            oid = u.get("open_id", "")
            if not oid:
                continue
            dpath = paths.get(did, "") if did != "0" else ""
            dname = dpath.split(" > ")[-1] if dpath else ""
            entry = people.get(oid) or {
                "open_id": oid, "name": u.get("name", ""),
                "job_title": u.get("job_title", ""),
                "department": dname, "department_path": dpath,
                "is_leader": oid in leader_ids,
            }
            if dpath and len(dpath) > len(entry.get("department_path") or ""):
                entry.update({"department": dname, "department_path": dpath})
            people[oid] = entry
            dept_members.setdefault(dpath or "(Trực thuộc công ty)", []).append(
                f"{u.get('name','?')}" + (f" — {u.get('job_title')}" if u.get("job_title") else ""))

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).isoformat()
    for entry in people.values():
        entry["updated_at"] = now
        db.sb().table("people").upsert(entry, on_conflict="open_id").execute()

    _write_org_chart(dept_members)
    db.sb().table("configs").upsert({
        "key": "org_sync", "value": {"last": now, "people": len(people),
                                     "departments": len(depts)},
        "description": "Trạng thái đồng bộ cơ cấu tổ chức (tự động hằng ngày)",
    }, on_conflict="key").execute()
    log.info("Đồng bộ tổ chức xong: %d người, %d phòng ban", len(people), len(depts))
    return {"people": len(people), "departments": len(depts)}


def _write_org_chart(dept_members: dict[str, list[str]]) -> None:
    """Sinh sơ đồ tổ chức .md trong kho bộ nhớ (thay file cũ)."""
    from . import lark_memory
    lines = ["# Sơ đồ tổ chức LSR",
             f"_Tự động sinh từ Lark Contacts — {lark_memory.today_vn()}_", ""]
    for dpath in sorted(dept_members):
        lines.append(f"## {dpath}")
        lines += [f"- {m}" for m in sorted(set(dept_members[dpath]))]
        lines.append("")
    content = "\n".join(lines)

    cfg = db.all_configs().get("org_chart_file", {})
    old_token = cfg.get("file_token", "")
    res = lark_memory.save_markdown("knowledge", "so-do-to-chuc.md", content)
    if res.get("status") == "uploaded":
        if old_token:
            try:
                lark_user.delete_drive_file(old_token)
            except Exception:
                log.warning("Không xóa được sơ đồ cũ %s", old_token)
        else:  # lần đầu: ghi INDEX
            lark_memory.append_index(
                f"- [knowledge/so-do-to-chuc.md] (token:{res.get('file_token','')}) · "
                f"{lark_memory.today_vn()} · Sơ đồ tổ chức LSR (tự cập nhật hằng ngày)")
        db.sb().table("configs").upsert({
            "key": "org_chart_file", "value": {"file_token": res.get("file_token", "")},
            "description": "Token file sơ đồ tổ chức trên Lark Drive (Jenny tự quản lý)",
        }, on_conflict="key").execute()


def get_person(open_id: str) -> dict | None:
    res = db.sb().table("people").select("*").eq("open_id", open_id).execute()
    return res.data[0] if res.data else None


def search_people(keyword: str, limit: int = 15) -> list[dict]:
    kw = f"%{keyword}%"
    res = (db.sb().table("people")
           .select("open_id,name,job_title,department,department_path,learned_notes")
           .or_(f"name.ilike.{kw},job_title.ilike.{kw},department.ilike.{kw},"
                f"department_path.ilike.{kw},learned_notes.ilike.{kw}")
           .limit(limit).execute())
    return res.data


def add_note(open_id: str, note: str) -> None:
    p = get_person(open_id)
    if p is None:
        raise RuntimeError("Không thấy người này trong danh bạ (chưa sync hoặc sai open_id)")
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).date().isoformat()
    notes = (p.get("learned_notes") or "").strip()
    notes = (notes + f"\n- [{today}] {note.strip()}").strip()
    db.sb().table("people").update({"learned_notes": notes[-4000:]}) \
        .eq("open_id", open_id).execute()
