"""BigQuery MCP server (in-process) cho agent.

2 tools:
- get_data_dictionary: đọc định nghĩa bảng từ wiki Lark (config `bq_data_dictionary`)
- bq_query: chạy SELECT trên BigQuery (read-only, giới hạn kết quả)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import db

log = logging.getLogger(__name__)

_dd_cache: dict = {"content": "", "at": 0.0}
DD_CACHE_SEC = 600
MAX_ROWS = 200


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def _dd_content() -> str:
    if time.time() - _dd_cache["at"] < DD_CACHE_SEC and _dd_cache["content"]:
        return _dd_cache["content"]
    cfg = db.all_configs().get("bq_data_dictionary", {})
    ref = cfg.get("url") or cfg.get("node_token") or ""
    if not ref:
        raise RuntimeError("Data dictionary chưa được cấu hình (config bq_data_dictionary).")
    from . import lark_user
    content = lark_user.read_document(ref)
    if cfg.get("project_id"):
        content = f"(GCP project mặc định: {cfg['project_id']})\n\n" + content
    _dd_cache.update({"content": content, "at": time.time()})
    return content


MAX_DD_CHARS = 40000


@tool("get_data_dictionary",
      "Đọc data dictionary BigQuery (định nghĩa bảng/cột) từ wiki Lark. LUÔN gọi trước khi "
      "viết SQL. Không tham số → trả MỤC LỤC các bảng. Truyền `section` (tên mục trong mục "
      "lục) để đọc 1 mục, hoặc `keyword` để lọc các dòng liên quan (vd 'tồn kho', 'doanh thu').",
      {"section": str, "keyword": str})
async def get_data_dictionary(args: dict) -> dict:
    try:
        content = _dd_content()
    except Exception as e:
        return _text(f"Không đọc được data dictionary: {e}")

    sections: dict[str, str] = {}
    current, buf = "(đầu tài liệu)", []
    for line in content.splitlines():
        if line.startswith("## "):
            sections[current] = "\n".join(buf)
            current, buf = line[3:].strip(), []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf)

    section = (args.get("section") or "").strip().lower()
    keyword = (args.get("keyword") or "").strip().lower()

    if section:
        for name, body in sections.items():
            if section in name.lower():
                return _text(f"## {name}\n{body[:MAX_DD_CHARS]}")
        return _text("Không thấy mục nào khớp. Mục lục: "
                     + " | ".join(sections.keys()))
    if keyword:
        hits = [ln for ln in content.splitlines() if keyword in ln.lower()]
        if not hits:
            return _text(f"Không có dòng nào chứa '{keyword}'. Mục lục: "
                         + " | ".join(sections.keys()))
        return _text("\n".join(hits)[:MAX_DD_CHARS])
    toc = [f"- {name} ({len(body):,} ký tự)" for name, body in sections.items()]
    return _text("MỤC LỤC data dictionary (gọi lại với section= hoặc keyword= để đọc chi tiết):\n"
                 + "\n".join(toc))


@tool("bq_query",
      "Chạy 1 câu SQL SELECT trên BigQuery và trả kết quả (tối đa 200 dòng). "
      "Chỉ SELECT/WITH — không DML/DDL. Luôn đọc data dictionary trước.",
      {"sql": str})
async def bq_query(args: dict) -> dict:
    sql = (args.get("sql") or "").strip().rstrip(";")
    if not re.match(r"^(select|with)\b", sql, re.I):
        return _text("Từ chối: chỉ chấp nhận câu lệnh SELECT/WITH (read-only).")
    if re.search(r"\b(insert|update|delete|merge|drop|create|alter|truncate|grant)\b", sql, re.I):
        return _text("Từ chối: SQL chứa từ khóa ghi/DDL — chỉ được đọc dữ liệu.")

    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds or not os.path.exists(creds):
        return _text("BigQuery chưa sẵn sàng: thiếu Google service account "
                     "(SETUP.md mục D). Báo người dùng biết, đừng bịa số liệu.")
    try:
        from google.cloud import bigquery
        cfg = db.all_configs().get("bq_data_dictionary", {})
        client = bigquery.Client(project=cfg.get("project_id") or None)
        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=20 * 1024**3)  # chặn query quét quá 20GB
        t0 = time.time()
        rows = list(client.query(sql, job_config=job_config).result(max_results=MAX_ROWS))
        cols = list(rows[0].keys()) if rows else []
        data = [dict(r) for r in rows]
        out = {
            "row_count": len(data),
            "truncated_at": MAX_ROWS if len(data) == MAX_ROWS else None,
            "duration_sec": round(time.time() - t0, 1),
            "columns": cols,
            "rows": data,
        }
        return _text(json.dumps(out, ensure_ascii=False, default=str)[:60000])
    except Exception as e:
        return _text(f"BigQuery lỗi: {e}")


@tool("bq_recent_queries",
      "Lấy lại các câu SQL BigQuery ĐÃ CHẠY thành công gần đây (để tái sử dụng đúng query "
      "cũ thay vì viết lại). keyword: lọc theo nội dung SQL (vd 'doanh thu', 'inventory', "
      "tên bảng). Trả về danh sách SQL kèm thời điểm — chọn câu phù hợp rồi chạy lại bằng "
      "bq_query. Dùng khi người dùng nói 'dùng lại query trước', 'cập nhật lại số đó'.",
      {"keyword": str})
async def bq_recent_queries(args: dict) -> dict:
    rows = db.recent_bq_queries(args.get("keyword", ""))
    if not rows:
        return _text("Chưa có query BigQuery nào khớp trong lịch sử.")
    return _text(json.dumps(rows, ensure_ascii=False, indent=1)[:20000])


@tool("anomaly_recent",
      "Lấy các CẢNH BÁO BẤT THƯỜNG số liệu mà hệ thống giám sát đã phát hiện gần đây "
      "(do thống kê phát hiện, đã kèm diễn giải). days: số ngày nhìn lại (mặc định 7). "
      "only_pending=true: chỉ lấy cảnh báo chưa gửi cho BOD (mức trung bình — dùng khi "
      "viết digest/brief để gom vào). Dùng khi viết brief sáng, digest thứ 2, hoặc khi "
      "được hỏi 'tuần này có gì bất thường'.",
      {"days": int, "only_pending": bool})
async def anomaly_recent(args: dict) -> dict:
    try:
        from . import anomaly
        rows = anomaly.recent_events(int(args.get("days") or 7),
                                     bool(args.get("only_pending")))
        if not rows:
            return _text("Không có cảnh báo bất thường nào trong khoảng này.")
        out = []
        for r in rows:
            out.append({
                "monitor": r.get("monitor_name"), "khi": str(r.get("created_at"))[:16],
                "muc_do": r.get("severity"), "gia_tri": r.get("value"),
                "muc_thuong_thay": r.get("baseline"), "lech_pct": r.get("pct_change"),
                "da_gui_BOD": r.get("notified"), "dien_giai": r.get("narrative"),
            })
        return _text(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:20000])
    except Exception as e:
        return _text(f"Lỗi: {e}")


bq_server = create_sdk_mcp_server(name="bq", version="1.0.0",
                                  tools=[get_data_dictionary, bq_query, bq_recent_queries,
                                         anomaly_recent])
