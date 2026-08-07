"""Agent core: gọi Claude Agent SDK, thu tool calls + token usage."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from . import config, db

log = logging.getLogger(__name__)

_bq = None


def _bq_server():
    global _bq
    if _bq is None:
        from .bq_tools import bq_server
        _bq = bq_server
    return _bq


_lark = None


def _lark_server():
    global _lark
    if _lark is None:
        from .lark_tools import lark_server
        _lark = lark_server
    return _lark

# Tool đọc web + BigQuery MCP (read-only). Không Bash/Write/Edit trên server.
ALLOWED_TOOLS = ["WebSearch", "WebFetch",
                 "mcp__bq__get_data_dictionary", "mcp__bq__bq_query",
                 "mcp__lark__read_lark_document",
                 "mcp__lark__calendar_list_events", "mcp__lark__calendar_create_event",
                 "mcp__lark__calendar_delete_event",
                 "mcp__lark__task_create", "mcp__lark__task_list", "mcp__lark__task_complete",
                 "mcp__lark__send_lark_message",
                 "mcp__lark__search_resources", "mcp__lark__recent_resources",
                 "mcp__lark__watch_document",
                 "mcp__lark__org_lookup", "mcp__lark__person_note_save",
                 "mcp__lark__meeting_list_pending", "mcp__lark__meeting_save_draft",
                 "mcp__lark__meeting_finalize",
                 "mcp__lark__notebooklm_ask", "mcp__lark__notebooklm_add_source",
                 "mcp__lark__notebooklm_audio_overview",
                 "mcp__lark__assignment_create", "mcp__lark__assignment_list",
                 "mcp__lark__assignment_update", "mcp__lark__assignment_remind",
                 "mcp__lark__assignment_notify_assigner",
                 "mcp__lark__memory_save", "mcp__lark__memory_index", "mcp__lark__memory_read"]
DISALLOWED_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit", "Task"]


@dataclass
class AgentReply:
    text: str = ""
    session_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)


def build_system_prompt(chat_title: str | None, is_group: bool,
                        channel: str = "Telegram") -> str:
    """Ghép persona + quy tắc (configs) + skills từ Supabase."""
    configs = db.all_configs()
    skills = db.enabled_skills()

    persona = configs.get("persona", {}).get(
        "text", "Bạn là Jenny, trợ lý AI của Ban điều hành LSR. Trả lời tiếng Việt, ngắn gọn.")
    reply_rules = configs.get("reply_rules", {})

    parts = [
        "# Vai trò thực tế (ghi đè ngữ cảnh mặc định)",
        "Bạn KHÔNG đang làm việc trong terminal với lập trình viên. Bạn đang "
        "trả lời tin nhắn chat cho lãnh đạo doanh nghiệp — mọi hướng dẫn về "
        "software engineering, code, commit chỉ áp dụng khi được hỏi đúng việc đó. "
        "Khi có xung đột, phần dưới đây được ưu tiên.",
        "",
        persona, "",
    ]

    company = configs.get("company_instructions", {}).get("text", "")
    if company:
        parts += [company, ""]

    parts.append("## Bối cảnh")
    parts.append(f"- Kênh: {channel} · {'group chat' if is_group else 'chat riêng'}"
                 + (f" · tên: {chat_title}" if chat_title else ""))
    if reply_rules:
        parts.append(f"- Quy tắc trả lời (config `reply_rules`): {reply_rules}")
    parts.append("")

    if skills:
        parts.append("## Skills")
        parts.append("Các năng lực bạn được trang bị. Chi tiết hay thay đổi luôn nằm ở "
                     "configs — tôn trọng trạng thái 'chưa cấu hình' nếu gặp.")
        for s in skills:
            parts.append(f"\n### {s['name']} — {s['description']}\n{s['content_md']}")

    return "\n".join(parts)


def build_prompt(history: list[dict], sender_name: str, text: str,
                 sender_context: str = "") -> str:
    parts = []
    if sender_context:
        parts.append(f"Thông tin người đang nhắn (từ danh bạ tổ chức): {sender_context}")
        parts.append("→ Điều chỉnh góc độ và mức chi tiết câu trả lời theo vai trò của họ.")
        parts.append("")
    if history:
        parts.append("Lịch sử hội thoại gần đây (cũ → mới):")
        for m in history:
            who = "Jenny" if m["direction"] == "out" else (m.get("sender_name") or "Người dùng")
            parts.append(f"[{who}]: {m['content'][:800]}")
        parts.append("")
    parts.append(f"Tin nhắn mới từ {sender_name}:\n{text}\n")
    parts.append("Hãy trả lời tin nhắn mới. Chỉ đưa ra nội dung trả lời, không lặp lại câu hỏi.")
    return "\n".join(parts)


async def run(prompt: str, system_prompt: str, conversation_id: str | None = None) -> AgentReply:
    reply = AgentReply()
    options = ClaudeAgentOptions(
        # Preset "claude_code": giữ TOÀN BỘ instructions gốc của Claude
        # (cách dùng tool, search, suy luận...), append thêm persona + skills.
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": system_prompt},
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        # KHÔNG dùng bypassPermissions (bị chặn khi chạy root trên VPS);
        # allowed_tools đã tự phê duyệt các tool trong danh sách.
        permission_mode="default",
        max_turns=15,
        # cwd trỏ vào thư mục app để Claude Code nạp .claude/settings.json
        # (hooks telemetry của LSR platform); setting_sources bắt buộc để SDK
        # đọc settings từ file thay vì bỏ qua.
        cwd=str(config.APP_DIR),
        setting_sources=["project"],
        mcp_servers={"bq": _bq_server(), "lark": _lark_server()},
    )
    last_text: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            last_text = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    last_text.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    reply.tool_calls.append({"name": block.name, "input": block.input})
                    db.log_tool_call(reply.session_id, conversation_id,
                                     block.name, block.input)
        elif isinstance(message, ResultMessage):
            reply.session_id = message.session_id
            reply.usage = message.usage or {}
            if not message.is_error and message.result:
                reply.text = message.result
            db.log_token_usage(message.session_id, getattr(message, "model", None),
                               reply.usage)

    if not reply.text:
        reply.text = "\n".join(last_text).strip() or "(Em chưa tạo được câu trả lời, anh/chị thử lại giúp em.)"
    return reply
