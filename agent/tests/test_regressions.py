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
    """Escape ở CẢ dòng tiêu đề VÀ dòng nội dung — thiếu chỗ nào cũng vỡ bảng."""
    from jenny import lark_user
    md = lark_user._rows_to_md([["Cột A | phụ"], ["có | ký tự"]], 30)
    assert r"Cột A \| phụ" in md, "dấu | ở dòng TIÊU ĐỀ không được escape"
    assert r"có \| ký tự" in md, "dấu | ở dòng NỘI DUNG không được escape"


def test_col_letter_dung_quy_uoc_bang_tinh():
    from jenny import lark_user
    assert [lark_user._col_letter(n) for n in (1, 26, 27, 52)] == ["A", "Z", "AA", "AZ"]


# ---------- A2A: ưu tiên platform, Lark chỉ là dự phòng ----------

def test_ask_uu_tien_platform_khi_da_bat(fake_db, monkeypatch):
    """Bật platform_a2a thì KHÔNG được đi qua Lark (không cần vào chat agent kia)."""
    from jenny import peers
    fake_db.configs["peer_agents"] = {"mino": {"role": "biên bản họp", "chat_id": "oc_x"}}
    fake_db.configs["platform_a2a"] = {"enabled": True, "url": "https://p/v1/a2a/{agent}/ask"}
    monkeypatch.setattr(peers, "_ask_platform",
                        lambda peer, q, cfg: {"status": "answered", "agent": peer["name"],
                                              "answer": "xong", "via": "platform",
                                              "waited_sec": 0})
    called = {"lark": False}
    fake_lark = types.ModuleType("jenny.lark_user")
    def _boom(*a, **k):
        called["lark"] = True
        raise AssertionError("không được nhắn Lark khi platform đã trả lời")
    fake_lark.send_text = _boom
    fake_lark.me = lambda: {"open_id": "ou_me"}
    fake_lark.list_messages = lambda *a, **k: []
    monkeypatch.setitem(sys.modules, "jenny.lark_user", fake_lark)

    res = peers.ask("mino", "họp S&OP chốt gì?")
    assert res["status"] == "answered" and res.get("via") == "platform"
    assert called["lark"] is False


def test_transport_platform_khong_fallback_sang_lark(fake_db, monkeypatch):
    """Peer khai transport='platform' mà lỗi thì báo lỗi, KHÔNG lặng lẽ nhắn Lark."""
    from jenny import peers
    fake_db.configs["peer_agents"] = {
        "mino": {"role": "biên bản họp", "chat_id": "oc_x", "transport": "platform"}}
    fake_db.configs["platform_a2a"] = {"enabled": True, "url": "https://p/x"}
    monkeypatch.setattr(peers, "_ask_platform",
                        lambda peer, q, cfg: {"status": "error", "agent": peer["name"],
                                              "error": "platform trả 403"})
    res = peers.ask("mino", "hỏi gì đó")
    assert res["status"] == "error" and "403" in res["error"]


def test_khong_co_duong_nao_thi_bao_ro_ca_hai(fake_db):
    from jenny import peers
    fake_db.configs["peer_agents"] = {"mino": {"role": "biên bản họp"}}
    fake_db.configs["platform_a2a"] = {"enabled": False}
    res = peers.ask("mino", "x")
    assert res["status"] == "error"
    assert "platform_a2a" in res["error"] and "chat_id" in res["error"]


# ---------- Sự cố 6: lịch trả vào thread bị schedule_list bỏ sót ----------
# Lịch trả kết quả vào thread có chat_id dạng "oc_xxx#om_yyy"; lọc theo chat_id thuần
# sẽ không khớp → Jenny báo "chat này không có lịch nào" trong khi vẫn có.

def test_schedule_list_thay_ca_lich_tra_vao_thread(fake_db):
    from jenny import lark_tools
    fake_db.store["scheduled_tasks"] = [
        {"id": "aaa11111-0000-0000-0000-000000000000", "name": "Lịch chat thường",
         "cron": "0 8 * * *", "enabled": True, "channel": "lark",
         "chat_id": "oc_abc", "prompt": "x"},
        {"id": "bbb22222-0000-0000-0000-000000000000", "name": "Lịch trong thread",
         "cron": "0 * * * *", "enabled": True, "channel": "lark",
         "chat_id": "oc_abc#om_thread1", "prompt": "y"},
        {"id": "ccc33333-0000-0000-0000-000000000000", "name": "Lịch chat khác",
         "cron": "0 9 * * *", "enabled": True, "channel": "lark",
         "chat_id": "oc_khac", "prompt": "z"}]
    out = _call(lark_tools.schedule_list, {"chat_id": "oc_abc"})
    assert "Lịch chat thường" in out
    assert "Lịch trong thread" in out, "bỏ sót lịch trả vào thread của cùng chat"
    assert "Lịch chat khác" not in out, "lọt lịch của chat khác"


# ---------- Sự cố 7: Jenny không ghi được nội dung vào tài liệu ----------

def test_co_tool_ghi_tai_lieu_va_duoc_cap_quyen():
    from jenny import agent, lark_tools
    assert hasattr(lark_tools, "write_lark_document"), "thiếu tool ghi tài liệu"
    assert "mcp__lark__write_lark_document" in agent.ALLOWED_TOOLS


def test_markdown_chuyen_thanh_block_dung_loai():
    from jenny import lark_user
    blocks = lark_user._md_to_blocks(
        "# H1\n## H2\n### H3\n- bullet\n1. ordered\n> quote\n---\n"
        "- [x] xong\n- [ ] chưa\n```\ncode here\n```\nđoạn văn")
    types = [b["block_type"] for b in blocks]
    assert types.count(3) == 1 and types.count(4) == 1 and types.count(5) == 1, types
    assert 12 in types and 13 in types and 15 in types, "thiếu bullet/ordered/quote"
    assert 22 in types, "thiếu divider"
    assert types.count(17) == 2, "thiếu todo"
    assert 14 in types, "thiếu code block"
    assert 2 in types, "thiếu đoạn văn thường"
    done = [b["todo"]["style"]["done"] for b in blocks if b["block_type"] == 17]
    assert done == [True, False], "trạng thái tick của todo sai"


def test_bo_dau_markdown_inline_khong_de_lot_ky_tu_tho():
    from jenny import lark_user
    blocks = lark_user._md_to_blocks("- Hapas **2,6 tỷ** kênh `online` xem [đây](http://x)")
    content = blocks[0]["bullet"]["elements"][0]["text_run"]["content"]
    assert "**" not in content and "`" not in content, content
    assert "2,6 tỷ" in content and "online" in content
    assert "đây (http://x)" in content, "link phải thành 'chữ (url)'"


def test_link_tai_lieu_khong_dung_domain_api(fake_db, monkeypatch):
    """LARK_DOMAIN là domain API (open.larksuite.com) — không được dùng làm link doc."""
    from jenny import lark_user
    fake_db.configs["lark_url_prefix"] = {"url": "https://tenant.sg.larksuite.com"}
    monkeypatch.setattr(lark_user, "_post",
                        lambda p, b=None, **k: {"document": {"document_id": "DOC123"}})
    doc = lark_user.create_document("X")
    assert doc["url"] == "https://tenant.sg.larksuite.com/docx/DOC123"
    assert "open.larksuite.com" not in doc["url"]


# ---------- Ghi tài liệu: hành vi của tool ----------

def test_write_doc_tao_moi_can_title_append_can_url(fake_db, monkeypatch):
    from jenny import lark_tools, lark_user
    monkeypatch.setattr(lark_user, "create_document",
                        lambda t, folder_token="": {"document_id": "D1",
                                                    "url": "https://t.sg.larksuite.com/docx/D1"})
    monkeypatch.setattr(lark_user, "append_markdown", lambda u, md: 3)

    assert "cần `title`" in _call(lark_tools.write_lark_document,
                                  {"mode": "create", "markdown": "# x"})
    assert "cần `url`" in _call(lark_tools.write_lark_document,
                                {"mode": "append", "markdown": "# x"})
    assert "Thiếu nội dung" in _call(lark_tools.write_lark_document,
                                     {"mode": "create", "title": "T", "markdown": "   "})


def test_write_doc_tra_link_va_tu_suy_ra_mode(fake_db, monkeypatch):
    """Có url mà không khai mode → phải hiểu là append, không tạo file mới."""
    from jenny import lark_tools, lark_user
    created = {"n": 0}
    def _create(t, folder_token=""):
        created["n"] += 1
        return {"document_id": "D1", "url": "https://t.sg.larksuite.com/docx/D1"}
    monkeypatch.setattr(lark_user, "create_document", _create)
    monkeypatch.setattr(lark_user, "append_markdown", lambda u, md: 2)

    out = _call(lark_tools.write_lark_document,
                {"url": "https://t.sg.larksuite.com/docx/DOC9", "markdown": "- a"})
    assert "ghi thêm" in out.lower() and "DOC9" in out
    assert created["n"] == 0, "không được tạo file mới khi chỉ định url"

    out2 = _call(lark_tools.write_lark_document, {"title": "Báo cáo", "markdown": "# a"})
    assert "Link: https://t.sg.larksuite.com/docx/D1" in out2
    assert created["n"] == 1


def test_append_markdown_gui_theo_lo_40_block(monkeypatch):
    from jenny import lark_user
    calls = []
    monkeypatch.setattr(lark_user, "_post",
                        lambda path, body=None, **k: calls.append(len(body["children"])) or {})
    md = "\n".join(f"- dòng {i}" for i in range(95))
    n = lark_user.append_markdown("DOC1", md)
    assert n == 95
    assert calls == [40, 40, 15], f"phải chia lô 40, thực tế {calls}"


def test_append_markdown_retry_roi_bao_ro_da_ghi_bao_nhieu(monkeypatch):
    """Lỗi giữa chừng phải nói rõ ghi được mấy block — đừng để tưởng ghi đủ."""
    from jenny import lark_user
    monkeypatch.setattr(lark_user.time, "sleep", lambda *_: None)
    state = {"n": 0}
    def _post(path, body=None, **k):
        size = len(body["children"])
        state["n"] += 1
        if size == 40 and state["n"] == 1:   # lô đầu: fail 1 lần rồi thành công
            raise RuntimeError("429 rate limit")
        if size == 10:                       # lô sau: fail cả 3 lần
            raise RuntimeError("500 server error")
        return {}
    monkeypatch.setattr(lark_user, "_post", _post)
    md = "\n".join(f"- d{i}" for i in range(50))     # 2 lô: 40 + 10
    try:
        lark_user.append_markdown("DOC1", md)
        raise AssertionError("phải raise khi lô sau thất bại")
    except RuntimeError as e:
        assert "40/50" in str(e), f"phải nêu số block đã ghi, được: {e}"


def test_doc_token_lay_dung_tu_link_va_token_tran(monkeypatch):
    from jenny import lark_user
    assert lark_user._doc_token("https://t.sg.larksuite.com/docx/ABC123") == "ABC123"
    assert lark_user._doc_token("ABC123") == "ABC123"
    monkeypatch.setattr(lark_user, "_get",
                        lambda p, params=None: {"node": {"obj_token": "REAL9"}})
    assert lark_user._doc_token("https://t.sg.larksuite.com/wiki/WIKI1") == "REAL9"


def test_delete_drive_file_truyen_dung_loai(monkeypatch):
    """Cứng type=file là sai cho docx/sheet — Lark sẽ từ chối."""
    from jenny import lark_user
    seen = []
    monkeypatch.setattr(lark_user, "_delete", lambda path: seen.append(path))
    lark_user.delete_drive_file("T1")
    lark_user.delete_drive_file("T2", "docx")
    lark_user.delete_drive_file("T3", "sheet")
    assert seen == ["/drive/v1/files/T1?type=file",
                    "/drive/v1/files/T2?type=docx",
                    "/drive/v1/files/T3?type=sheet"], seen


def test_khong_do_duoc_domain_thi_dung_applink_chu_khong_ra_link_hong(fake_db, monkeypatch):
    from jenny import lark_user
    monkeypatch.setattr(lark_user, "_post",
                        lambda p, b=None, **k: {"document": {"document_id": "D7"}})
    monkeypatch.setattr(lark_user, "doc_url_prefix", lambda: "")
    url = lark_user.create_document("X")["url"]
    assert url == "https://applink.larksuite.com/client/docs/open?docToken=D7"


# ---------- Typing = react emoji ----------

def test_typing_mac_dinh_la_reaction_va_giu_dau_sau_khi_tra_loi(fake_db):
    from jenny import lark_user_bot
    cfg = lark_user_bot._typing_cfg()
    assert cfg["mode"] == "reaction"
    assert cfg["emoji"] == "OK"
    assert cfg["remove_after_reply"] is False, "mặc định phải GIỮ dấu OK làm vết đã xem"


def test_typing_mode_off_thi_khong_tha_reaction(fake_db, monkeypatch):
    from jenny import lark_user, lark_user_bot
    fake_db.configs["typing"] = {"mode": "off"}
    called = {"n": 0}
    monkeypatch.setattr(lark_user, "add_reaction",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "rid")
    assert lark_user_bot._react_ack("om_x", lark_user_bot._typing_cfg()) == ""
    assert called["n"] == 0


def test_reaction_loi_khong_duoc_chan_viec_tra_loi(fake_db, monkeypatch):
    from jenny import lark_user, lark_user_bot
    monkeypatch.setattr(lark_user, "add_reaction",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("231001")))
    assert lark_user_bot._react_ack("om_x", lark_user_bot._typing_cfg()) == ""


# ---------- Voice note: lọc để không gỡ băng tràn lan ----------

def test_voice_trong_group_khong_reply_thi_bo_qua(fake_db, monkeypatch):
    """Tin thoại giữa group không liên quan Jenny thì KHÔNG được tốn tiền gỡ băng."""
    from jenny import lark_user_bot
    goi = {"n": 0}
    fake_tr = types.ModuleType("jenny.transcribe")
    fake_tr.transcribe_audio = lambda *a, **k: goi.__setitem__("n", 1) or "text"
    fake_tr.to_wav = lambda d, src_ext=".opus": d
    monkeypatch.setitem(sys.modules, "jenny.transcribe", fake_tr)

    group = {"chat_id": "oc_a", "chat_mode": "group", "chat_type": "group"}
    msg = {"message_id": "om_1", "msg_type": "audio",
           "body": {"content": '{"file_key":"k","duration":3000}'}}
    assert lark_user_bot._voice_transcript(group, msg, in_thread=False) == ""
    assert goi["n"] == 0, "đã gọi Whisper dù tin không liên quan Jenny"


def test_voice_qua_dai_thi_bo_qua(fake_db, monkeypatch):
    from jenny import lark_user_bot
    fake_db.configs["voice_note"] = {"max_duration_sec": 60}
    p2p = {"chat_id": "oc_a", "chat_mode": "p2p", "chat_type": "p2p"}
    msg = {"message_id": "om_1", "msg_type": "audio",
           "body": {"content": '{"file_key":"k","duration":120000}'}}   # 120 giây
    assert lark_user_bot._voice_transcript(p2p, msg, in_thread=False) == ""


# ---------- Tỷ lệ hoàn thành cam kết ----------

def test_followthrough_dem_dung_dung_han_va_tre(fake_db):
    from jenny import assignments
    now = dt.datetime.now(assignments.VN)
    hom_qua = (now - dt.timedelta(days=1)).isoformat()
    truoc_han = (now - dt.timedelta(days=2)).isoformat()
    sau_han = (now - dt.timedelta(hours=1)).isoformat()
    fake_db.store["assignments"] = [
        {"id": "a1", "title": "Đúng hạn", "status": "done", "deadline": hom_qua,
         "completed_at": truoc_han, "pic_name": "A"},
        {"id": "a2", "title": "Trễ", "status": "done", "deadline": hom_qua,
         "completed_at": sau_han, "pic_name": "B"},
        {"id": "a3", "title": "Chưa xong", "status": "assigned", "deadline": hom_qua,
         "pic_name": "C"},
    ]
    s = assignments.followthrough_stats(7)
    assert s["total_due"] == 3
    assert s["on_time"] == 1 and s["late"] == 1 and s["still_open"] == 1, s
    assert s["rate_pct"] == 33, s
