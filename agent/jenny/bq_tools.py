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


bq_server = create_sdk_mcp_server(name="bq", version="1.0.0",
                                  tools=[get_data_dictionary, bq_query])
