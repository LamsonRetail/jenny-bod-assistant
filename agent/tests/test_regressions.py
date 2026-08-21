"""Test chống tái diễn các lỗi ĐÃ TỪNG xảy ra thật trên production.

Mỗi test gắn với một sự cố cụ thể — sửa code mà làm hỏng lại thì test đỏ ngay.
Chạy:  cd agent && python3 -m pytest tests/ -q
Không cần Supabase/Lark thật: mọi truy cập DB/API đều được thay bằng bản giả.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------- hạ tầng giả ----------

class FakeTable:
    def __init__(self, store: dict, name: str):
        self.store, self.name = store, name
        self._rows = list(store.get(name, []))
        self._patch: dict | None = None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def eq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self
    def neq(self, col, val):
        self._rows = [r for r in self._rows if r.get(col) != val]
        return self
    def in_(self, col, vals):
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self
    def is_(self, *a, **k): return self

    def update(self, patch):
        self._patch = patch
        return self

    def execute(self):
        if self._patch is not None:
            for r in self._rows:
                r.update(self._patch)
            for real in self.store.get(self.name, []):
                for r in self._rows:
                    if real.get("id") == r.get("id"):
                        real.update(self._patch)
        return types.SimpleNamespace(data=self._rows)


class FakeSB:
    def __init__(self, store): self.store = store
    def table(self, name): return FakeTable(self.store, name)


@pytest.fixture
def fake_db(monkeypatch):
    """db.sb() + db.all_configs() giả, có thể nạp dữ liệu tuỳ test."""
    from jenny import db
    store: dict = {"scheduled_tasks": [], "meetings": [], "people": []}
    configs: dict = {}
    monkeypatch.setattr(db, "sb", lambda: FakeSB(store))
    monkeypatch.setattr(db, "all_configs", lambda: configs)
    return types.SimpleNamespace(store=store, configs=configs)


def _call(tool, args):
    """Gọi 1 @tool và lấy text trả về."""
    import asyncio
    res = asyncio.run(tool.handler(args))
    return res["content"][0]["text"]


# ---------- Sự cố 1: yêu cầu đổi báo cáo định kỳ nhưng cron không đổi ----------
# 2026-08-20: người dùng yêu cầu bỏ phần họp khỏi "Tổng kết cuối ngày". Jenny trả lời
# đã bỏ nhưng hôm sau vẫn thấy — vì KHÔNG có tool nào sửa được lịch (chỉ create/list/
# delete), nên prompt của cron giữ nguyên.

def test_schedule_update_ton_tai_va_duoc_dang_ky():
    """Phải có tool sửa lịch, và phải nằm trong ALLOWED_TOOLS."""
    from jenny import agent, lark_tools
    names = [t.name for t in lark_tools.lark_server.tools] \
        if hasattr(lark_tools.lark_server, "tools") else []
    assert hasattr(lark_tools, "schedule_update"), "thiếu tool schedule_update"
    assert "mcp__lark__schedule_update" in agent.ALLOWED_TOOLS, \
        "schedule_update chưa được cấp quyền trong ALLOWED_TOOLS"
    if names:
        assert "schedule_update" in names, "schedule_update chưa đăng ký vào MCP server"


def test_schedule_update_xoa_duoc_mot_dong_trong_prompt(fake_db):
    from jenny import lark_tools
    fake_db.store["scheduled_tasks"] = [{
        "id": "72c76cad-0000-0000-0000-000000000000",
        "name": "Tổng kết task cuối ngày cho BOD",
        "cron": "0 18 * * 1-6", "enabled": True,
        "prompt": ("1) assignment_list: việc đang mở.\n"
                   "2) assignment_stats: tỷ lệ hoàn thành.\n"
                   "3) meeting_list_pending: họp còn chờ nội dung/duyệt notes thì nhắc.\n"
                   "4) Kết bằng đề xuất."),
    }]
    out = _call(lark_tools.schedule_update,
                {"schedule_id": "72c76cad", "prompt_remove": "meeting_list_pending"})
    saved = fake_db.store["scheduled_tasks"][0]["prompt"]
    assert "meeting_list_pending" not in saved, "dòng về họp vẫn còn trong prompt đã lưu"
    assert "assignment_list" in saved and "Kết bằng đề xuất" in saved, "xoá lố nội dung khác"
    assert "PROMPT MỚI" in out, "phải trả prompt mới để agent xác nhận lại với người dùng"


def test_schedule_update_bao_loi_khi_khong_khop_thay_vi_im_lang(fake_db):
    """Không khớp đoạn cần xoá thì phải nói rõ — tránh 'im lặng coi như đã sửa'."""
    from jenny import lark_tools
    fake_db.store["scheduled_tasks"] = [{
        "id": "abcd1234-0000-0000-0000-000000000000", "name": "Brief sáng",
        "cron": "30 7 * * 1-6", "enabled": True, "prompt": "1) Doanh thu hôm qua."}]
    out = _call(lark_tools.schedule_update,
                {"schedule_id": "abcd1234", "prompt_remove": "phần không tồn tại"})
    assert "Không thấy đoạn" in out
    assert fake_db.store["scheduled_tasks"][0]["prompt"] == "1) Doanh thu hôm qua."


def test_schedule_update_chan_cron_sai_dinh_dang(fake_db):
    from jenny import lark_tools
    fake_db.store["scheduled_tasks"] = [{
        "id": "abcd1234-0000-0000-0000-000000000000", "name": "X", "cron": "0 8 * * *",
        "enabled": True, "prompt": "y"}]
    out = _call(lark_tools.schedule_update, {"schedule_id": "abcd1234", "cron": "0 8 *"})
    assert "5 trường" in out
    assert fake_db.store["scheduled_tasks"][0]["cron"] == "0 8 * * *", "cron sai vẫn bị ghi"


# ---------- Sự cố 2: báo cáo vẫn chèn nội dung họp dù đã giao cho Mino ----------

def test_meeting_list_pending_im_lang_o_che_do_delegate(fake_db):
    """Giao biên bản cho Mino thì tool phải từ chối trả danh sách họp chờ nội dung."""
    from jenny import lark_tools
    fake_db.configs["meeting_notes"] = {"mode": "delegate"}
    fake_db.store["meetings"] = [
        {"id": "m1", "title": "Họp tài chính", "status": "awaiting_content"},
        {"id": "m2", "title": "HỌP 1-1", "status": "awaiting_content"}]
    out = _call(lark_tools.meeting_list_pending, {})
    assert "MINO" in out.upper(), "phải nói rõ biên bản do Mino phụ trách"
    assert "KHÔNG đưa mục này vào báo cáo" in out
    assert "Họp tài chính" not in out, "vẫn rò danh sách họp ra báo cáo"


def test_meeting_list_pending_van_hoat_dong_o_che_do_self(fake_db):
    """Đổi về mode='self' thì tính năng cũ phải chạy lại được."""
    from jenny import lark_tools, meetings
    fake_db.configs["meeting_notes"] = {"mode": "self"}
    fake_db.store["meetings"] = [{
        "id": "m1", "title": "Họp tài chính", "status": "awaiting_content",
        "end_at": "2026-08-13T05:00:00+00:00", "creator_name": "A",
        "creator_open_id": "ou_x", "notes_md": ""}]
    assert meetings.self_transcribe() is True
    out = _call(lark_tools.meeting_list_pending, {})
    assert "Họp tài chính" in out


def test_ba_cua_vao_pipeline_go_bang_deu_bi_chan_o_delegate(fake_db):
    """maybe_watch / file audio trong chat / event bản ghi VC — cả ba phải bị chặn."""
    from jenny import meetings
    fake_db.configs["meeting_notes"] = {"mode": "delegate"}
    assert meetings.self_transcribe() is False
    assert meetings.notes_mode() == "delegate"
    # cửa 2: file audio gửi trong chat
    from jenny import lark_user_bot
    handled = lark_user_bot._maybe_meeting_recording(
        {"msg_type": "file", "message_id": "om_x",
         "body": {"content": '{"file_key":"k","file_name":"a.m4a"}'}}, "", "ou_a")
    assert handled is False, "vẫn nhận file ghi âm dù đã giao cho Mino"


# ---------- Sự cố 3: mở A2A cho mọi agent trong platform ----------

def test_allow_all_thi_bot_duoc_duyet_nguoi_thi_khong(fake_db, monkeypatch):
    from jenny import capabilities
    fake_db.configs["a2a_allowed_agents"] = {"open_ids": [], "allow_all": True}
    fake = types.ModuleType("jenny.mentionables")
    fake.classify_bots = lambda ids: {i: i.startswith("ou_bot") for i in ids}
    monkeypatch.setitem(sys.modules, "jenny.mentionables", fake)

    assert capabilities.a2a_open_to_all() is True
    assert capabilities.is_agent_sender("ou_bot_mino") is True, "agent phải được duyệt"
    assert capabilities.is_agent_sender("ou_nguoi_that") is False, \
        "người thật KHÔNG được tự duyệt qua đường A2A"


def test_tat_allow_all_thi_chi_agent_khai_ten_duoc_vao(fake_db):
    from jenny import capabilities
    fake_db.configs["a2a_allowed_agents"] = {"open_ids": ["ou_mino"], "allow_all": False}
    assert capabilities.is_agent_sender("ou_mino") is True
    assert capabilities.is_agent_sender("ou_bot_khac") is False


# ---------- Sự cố 4: cảnh báo bất thường bắn sai ----------
# Chuỗi phẳng làm MAD≈0 → lệch 1% cũng thành mức cao; và campaign tăng mạnh bị báo
# dù BOD chỉ cần biết khi GIẢM.

def _series(vals, start=dt.date(2026, 6, 1)):
    return [{"d": start + dt.timedelta(days=i), "v": v} for i, v in enumerate(vals)]


def test_san_nhieu_chan_bao_dong_gia_tren_chuoi_phang():
    from jenny import anomaly
    base = []
    for _ in range(8):
        base += [100, 90, 95, 100, 130, 150, 140]
    mon = {"threshold_high": 3.0, "threshold_med": 2.0, "direction": "both"}
    ev = anomaly.evaluate(_series(base + [100, 90, 96]))
    assert anomaly._severity(ev["z"], mon) is None, "lệch 1% không được coi là bất thường"
    ev2 = anomaly.evaluate(_series(base + [100, 90, 55]))
    assert anomaly._severity(ev2["z"], mon) == "high", "tụt 42% phải báo mức cao"


def test_direction_down_khong_bao_khi_tang():
    from jenny import anomaly
    base = []
    for _ in range(8):
        base += [100, 90, 95, 100, 130, 150, 140]
    down = {"threshold_high": 3.0, "threshold_med": 2.0, "direction": "down"}
    ev = anomaly.evaluate(_series(base + [100, 90, 300]))
    assert anomaly._severity(ev["z"], down) is None, \
        "doanh thu TĂNG mạnh (campaign) không được đánh thức BOD"
    ev2 = anomaly.evaluate(_series(base + [100, 90, 40]))
    assert anomaly._severity(ev2["z"], down) == "high"


def test_ngay_campaign_trong_blackout_bi_chan(fake_db):
    from jenny import anomaly
    fake_db.configs["anomaly_defaults"] = {"blackout_dates": ["08-08"]}
    d = anomaly._defaults()
    assert anomaly._in_blackout(dt.date(2026, 8, 8), d) is True
    assert anomaly._in_blackout(dt.date(2026, 8, 12), d) is False


# ---------- Sự cố 5: đọc bảng tính in ra cả rừng cột trống ----------

def test_doc_sheet_bo_dong_va_cot_trong_o_cuoi():
    from jenny import lark_user
    values = [["Brand", "Doanh thu", "", "", ""],
              ["HPVN", "2.6", "", "", ""],
              ["", "", "", "", ""]]
    md = lark_user._rows_to_md(values, 30)
    assert "C3" not in md and "C4" not in md, "vẫn in cột trống ở cuối"
    assert md.count("\n") == 2, "không cắt dòng trống ở cuối"
    assert "Brand" in md and "HPVN" in md


def test_doc_sheet_escape_dau_gach_dung():
    from jenny import lark_user
    md = lark_user._rows_to_md([["A"], ["có | ký tự"]], 30)
    assert r"có \| ký tự" in md, "dấu | không escape sẽ làm vỡ bảng markdown"


def test_col_letter_dung_quy_uoc_bang_tinh():
    from jenny import lark_user
    assert [lark_user._col_letter(n) for n in (1, 26, 27, 52)] == ["A", "Z", "AA", "AZ"]
