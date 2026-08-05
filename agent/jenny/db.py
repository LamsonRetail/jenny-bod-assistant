"""Tầng dữ liệu Supabase: hội thoại, log, skills, configs."""
from __future__ import annotations

import datetime as dt
import json
import logging
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from . import config

log = logging.getLogger(__name__)

_client: Client | None = None


def sb() -> Client:
    global _client
    if _client is None:
        config.require("SUPABASE_URL", "SUPABASE_SERVICE_KEY")
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
    return _client


# ---------- Hội thoại & tin nhắn ----------

def get_or_create_conversation(channel: str, chat_id: str, title: str | None,
                               is_group: bool) -> dict:
    res = (sb().table("conversations").select("*")
           .eq("channel", channel).eq("chat_id", chat_id).execute())
    if res.data:
        conv = res.data[0]
        if title and conv.get("title") != title:
            sb().table("conversations").update({"title": title}).eq("id", conv["id"]).execute()
        return conv
    res = (sb().table("conversations")
           .insert({"channel": channel, "chat_id": chat_id, "title": title,
                    "is_group": is_group, "whitelisted": False})
           .execute())
    return res.data[0]


def set_whitelisted(conversation_id: str, value: bool) -> None:
    sb().table("conversations").update({"whitelisted": value}).eq("id", conversation_id).execute()


def log_message(conversation_id: str, direction: str, content: str,
                sender_id: str | None = None, sender_name: str | None = None,
                session_id: str | None = None,
                tokens_input: int | None = None, tokens_output: int | None = None) -> None:
    sb().table("messages").insert({
        "conversation_id": conversation_id, "direction": direction,
        "content": content[:20000], "sender_id": sender_id, "sender_name": sender_name,
        "session_id": session_id,
        "tokens_input": tokens_input, "tokens_output": tokens_output,
    }).execute()


def recent_messages(conversation_id: str, limit: int) -> list[dict]:
    res = (sb().table("messages")
           .select("direction,sender_name,content,created_at")
           .eq("conversation_id", conversation_id)
           .order("created_at", desc=True).limit(limit).execute())
    return list(reversed(res.data))


# ---------- Log vận hành ----------

def log_tool_call(session_id: str | None, conversation_id: str | None,
                  tool_name: str, args: dict | None,
                  status: str = "ok", error: str | None = None,
                  result_summary: str | None = None) -> None:
    try:
        sb().table("tool_calls").insert({
            "session_id": session_id, "conversation_id": conversation_id,
            "tool_name": tool_name,
            "args": json.loads(json.dumps(args, default=str)) if args else None,
            "status": status, "error": error, "result_summary": result_summary,
        }).execute()
    except Exception:  # log vận hành không được làm hỏng luồng trả lời
        log.exception("log_tool_call failed")


def log_token_usage(session_id: str | None, model: str | None, usage: dict[str, Any]) -> None:
    try:
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).date().isoformat()
        sb().table("token_usage").insert({
            "day": today, "session_id": session_id, "model": model,
            "input_tokens": usage.get("input_tokens", 0) or 0,
            "output_tokens": usage.get("output_tokens", 0) or 0,
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        }).execute()
    except Exception:
        log.exception("log_token_usage failed")


# ---------- Danh bạ tài nguyên (link/file share trong chat) ----------

def save_resource(row: dict) -> None:
    try:
        sb().table("resources").upsert(row, on_conflict="url,chat_id").execute()
    except Exception:
        log.exception("save_resource failed (đã chạy migration 0003 chưa?)")


def search_resources(keyword: str, limit: int = 20) -> list[dict]:
    kw = f"%{keyword}%"
    res = (sb().table("resources")
           .select("kind,url,file_token,title,excerpt,context_note,chat_name,created_at")
           .or_(f"title.ilike.{kw},url.ilike.{kw},excerpt.ilike.{kw},context_note.ilike.{kw}")
           .order("created_at", desc=True).limit(limit).execute())
    return res.data


def recent_resources(limit: int = 20) -> list[dict]:
    res = (sb().table("resources")
           .select("kind,url,file_token,title,excerpt,context_note,chat_name,created_at")
           .order("created_at", desc=True).limit(limit).execute())
    return res.data


# ---------- Skills & configs (cache ngắn, dashboard sửa là phiên mới ăn ngay) ----------

def enabled_skills() -> list[dict]:
    res = (sb().table("skills").select("name,description,content_md")
           .eq("enabled", True).order("name").execute())
    return res.data


def all_configs() -> dict[str, Any]:
    res = sb().table("configs").select("key,value").execute()
    return {row["key"]: row["value"] for row in res.data}
