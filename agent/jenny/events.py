"""Bot event listener (WebSocket) — đường tenant để Jenny biết việc xảy ra.

Vì Lark chặn user token đọc Task/Minutes, ta dùng BOT (app) nhận event tenant:
- vc.meeting.recording_ready_v1  → bản ghi họp sẵn sàng → chạy pipeline notes
- vc.meeting.all_meeting_ended_v1 → cuộc họp kết thúc (dự phòng phát hiện họp)
- task.task.updated_tenant_v1     → task trong tenant thay đổi (comment/tiến độ)

Payload event được log đầy đủ lần đầu để biết Lark cấp những gì.
"""
from __future__ import annotations

import json
import logging
import threading

import lark_oapi as lark

from . import config, db

log = logging.getLogger(__name__)


def _log_event(kind: str, payload: dict) -> None:
    db.log_tool_call(None, None, f"event_{kind}",
                     {"payload": json.loads(json.dumps(payload, default=str))[:1]
                      if False else payload},
                     result_summary="received")


def _on_recording_ready(data: lark.CustomizedEvent) -> None:
    raw = lark.JSON.marshal(data)
    log.info("EVENT recording_ready: %s", raw[:800])
    threading.Thread(target=_handle_recording, args=(raw,), daemon=True).start()


def _handle_recording(raw: str) -> None:
    from . import lark_user, meetings
    try:
        ev = json.loads(raw).get("event", {}) or json.loads(raw)
        meeting = ev.get("meeting", {}) or {}
        meeting_id = meeting.get("id") or ev.get("meeting_id") or ""
        topic = meeting.get("topic") or "(không tiêu đề)"
        url = (ev.get("url") or "").strip()
        log.info("Bản ghi sẵn sàng: '%s' (meeting_id=%s) url=%s", topic, meeting_id, url)

        if not url and meeting_id:
            data = lark_user.vc_recording(meeting_id)
            url = (data.get("recording", {}) or {}).get("url", "")
        if not url:
            log.warning("Không lấy được URL bản ghi cho '%s'", topic)
            return

        row = meetings.match_or_create_by_topic(topic, meeting)
        if not row:
            log.info("Bỏ qua bản ghi '%s' — không khớp cuộc họp nào Jenny theo dõi", topic)
            return
        meetings.process_recording(row, minutes_url=url)
    except Exception:
        log.exception("Xử lý recording_ready lỗi")


def _on_meeting_ended(data: lark.CustomizedEvent) -> None:
    log.info("EVENT meeting_ended: %s", lark.JSON.marshal(data)[:500])


def _on_task_updated(data: lark.CustomizedEvent) -> None:
    raw = lark.JSON.marshal(data)
    log.info("EVENT task_updated: %s", raw[:600])
    threading.Thread(target=_handle_task_event, args=(raw,), daemon=True).start()


def _handle_task_event(raw: str) -> None:
    """Task tenant thay đổi → thử đọc comment mới bằng tenant token."""
    from . import doc_watch, lark_user
    try:
        ev = json.loads(raw).get("event", {}) or json.loads(raw)
        guid = (ev.get("task_id") or ev.get("task", {}).get("guid")
                or ev.get("obj_id") or "")
        if not guid:
            return
        try:
            comments = lark_user.list_task_comments(guid)
        except Exception as e:
            log.info("Task %s: bot chưa có quyền đọc (%s)", guid[:12], str(e)[:80])
            return
        log.info("Task %s có %d comment — xử lý", guid[:12], len(comments))
        doc_watch.handle_task_comments(guid, comments)
    except Exception:
        log.exception("Xử lý task event lỗi")


def run_listener() -> None:
    config.require("LARK_APP_ID", "LARK_APP_SECRET")
    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_customized_event("vc.meeting.recording_ready_v1",
                                             _on_recording_ready)
               .register_p2_customized_event("vc.meeting.all_meeting_ended_v1",
                                             _on_meeting_ended)
               .register_p2_customized_event("task.task.updated_tenant_v1",
                                             _on_task_updated)
               .build())
    ws = lark.ws.Client(config.LARK_APP_ID, config.LARK_APP_SECRET,
                        event_handler=handler, domain=config.LARK_DOMAIN,
                        log_level=lark.LogLevel.INFO)
    log.info("Jenny event listener (bot/tenant): bắt đầu WebSocket…")
    ws.start()
