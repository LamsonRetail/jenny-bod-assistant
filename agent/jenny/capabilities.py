"""Bản khai năng lực của Jenny + hướng dẫn agent khác hỏi trực tiếp (A2A).

Một nguồn duy nhất, dùng cho 3 nơi:
  1. `GET /.well-known/agent.json` trên jenny-web — agent khác đọc bằng máy (Agent Card).
  2. `GET /a2a` — bản cho người/agent đọc bằng chữ.
  3. Đẩy về LSR platform qua trace (`lsr_telemetry.send(extra=…)`) — platform chưa mở
     API đăng ký năng lực (mọi endpoint trả 403 với token telemetry), nên kênh trace là
     đường duy nhất có xác thực để platform biết Jenny làm được gì.

Sửa danh sách năng lực ở đây → cả 3 nơi tự cập nhật.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from . import db

log = logging.getLogger(__name__)

VN = dt.timezone(dt.timedelta(hours=7))
AGENT_ID = os.environ.get("LSR_AGENT_ID", "AG-JENNY-BOD")
VERSION = "1.0.0"

# open_id tài khoản Lark của Jenny — địa chỉ để agent khác tag/nhắn
JENNY_OPEN_ID = "ou_9d44d808d8d1624b217a35abb5b80130"

CAPABILITIES: list[dict] = [
    {
        "id": "business_data_query",
        "name": "Tra số liệu kinh doanh (BigQuery)",
        "description": "Trả lời câu hỏi về doanh thu, đơn hàng, tồn kho, quảng cáo, "
                       "kế hoạch/target của LSR bằng cách query BigQuery theo data "
                       "dictionary nội bộ. Chỉ đọc, không ghi.",
        "inputs": "Câu hỏi bằng tiếng Việt, nêu rõ chỉ số · khoảng thời gian · brand/kênh",
        "outputs": "Số liệu kèm diễn giải kinh doanh, có nêu khoảng thời gian dữ liệu",
        "example": "Doanh thu thuần theo đơn tạo hôm qua của Hapas VN là bao nhiêu, "
                   "so với trung bình 7 ngày?",
        "limits": "Chỉ dữ liệu LSR trong BigQuery. Không bịa số khi không có dữ liệu.",
    },
    {
        "id": "target_vs_actual",
        "name": "Đối chiếu thực tế vs kế hoạch",
        "description": "So doanh thu thực tế với target theo brand/ngày, tính % hoàn "
                       "thành và dự báo theo tốc độ bán hiện tại.",
        "inputs": "Brand (HPVN/MMVN/HPTH/MMTL) và kỳ cần xem",
        "outputs": "Thực tế · target · % hoàn thành · dự báo cuối kỳ · độ phủ dữ liệu",
        "example": "MMVN tháng này đạt bao nhiêu % target, dự báo cuối tháng?",
        "limits": "Nêu rõ target chỉ phủ tới ngày nào, không suy diễn thành target cả năm.",
    },
    {
        "id": "anomaly_watch",
        "name": "Giám sát & cảnh báo bất thường",
        "description": "Theo dõi chỉ số theo lịch, phát hiện lệch bất thường bằng thống "
                       "kê (median+MAD so cùng thứ trong tuần) rồi diễn giải nguyên nhân.",
        "inputs": "Chỉ số cần theo dõi + ngưỡng, hoặc câu hỏi 'tuần này có gì bất thường'",
        "outputs": "Danh sách cảnh báo kèm giá trị, mức thường thấy, mức độ, diễn giải",
        "example": "Tuần qua có cảnh báo bất thường nào về doanh thu không?",
        "limits": "Bỏ qua ngày campaign trong danh sách blackout để tránh báo động giả.",
    },
    {
        "id": "org_directory",
        "name": "Tra danh bạ tổ chức",
        "description": "Tìm người theo tên/chức danh/phòng ban trong LSR (sync từ Lark "
                       "Contacts hằng ngày), kèm ghi chú công việc đã học.",
        "inputs": "Từ khoá tên, chức danh hoặc phòng ban",
        "outputs": "Tên · chức danh · phòng ban · open_id để tag",
        "example": "Ai phụ trách kế hoạch hàng hoá (KHHH)?",
        "limits": "Chỉ thông tin công việc, không trả lời chuyện đời tư hay đánh giá cá nhân.",
    },
    {
        "id": "document_read",
        "name": "Đọc tài liệu Lark",
        "description": "Đọc wiki, docx, Base và Sheet (bảng tính) bằng quyền tài khoản "
                       "Jenny; trả về nội dung dạng chữ/bảng markdown.",
        "inputs": "Link tài liệu Lark",
        "outputs": "Nội dung tài liệu (bảng tính trả theo từng trang)",
        "example": "Đọc giúp file bảng tính S&OP này và tóm tắt phần Mate Made.",
        "limits": "File phải được share cho account Jenny. Bảng tính giới hạn "
                  "200 dòng × 30 cột × 8 trang mỗi lần đọc.",
    },
    {
        "id": "assignment_tracking",
        "name": "Giao việc & đôn đốc",
        "description": "Ghi việc BOD giao (đủ bối cảnh, đầu ra mong đợi, PIC, deadline), "
                       "tạo Lark task, tự nhắc trước hạn và khi quá hạn, escalate khi cần.",
        "inputs": "Nội dung việc + người phụ trách + deadline (hoặc hỏi tình trạng)",
        "outputs": "Mã việc, trạng thái, tỷ lệ hoàn thành đúng hạn",
        "example": "Còn việc nào của phòng cung ứng đang quá hạn không?",
        "limits": "Chỉ thành viên BOD mới giao được việc mới.",
    },
    {
        "id": "decision_log",
        "name": "Sổ quyết định & đo kết quả",
        "description": "Ghi quyết định kèm kỳ vọng đo được và mốc nhìn lại; đến hạn tự "
                       "chạy lại số liệu để đối chiếu kỳ vọng vs thực tế.",
        "inputs": "Nội dung quyết định, kỳ vọng, mốc đo lại — hoặc câu hỏi tra cứu",
        "outputs": "Danh sách quyết định, kết quả thực tế, bài học",
        "example": "Ta đã quyết gì về chính sách freeship, kết quả thế nào?",
        "limits": "Chỉ ghi sau khi người quyết xác nhận.",
    },
    {
        "id": "market_research",
        "name": "Tổng hợp thông tin thị trường",
        "description": "Tìm và tổng hợp tin thị trường, đối thủ ngành túi xách / TMĐT "
                       "Việt Nam - Đông Nam Á, luôn kèm nguồn.",
        "inputs": "Chủ đề cần tìm",
        "outputs": "2-5 điểm chính kèm link nguồn và thời điểm",
        "example": "Tuần này có tin gì đáng chú ý về TMĐT Việt Nam?",
        "limits": "Không kết luận chắc chắn từ nguồn không đáng tin.",
    },
]

# Việc Jenny KHÔNG làm — khai rõ để agent khác không hỏi sai chỗ
NOT_SUPPORTED = [
    {"id": "meeting_notes", "reason": "Biên bản họp do agent MINO phụ trách. "
                                      "Jenny không gỡ băng, chỉ hỏi lại Mino khi cần."},
    {"id": "write_data", "reason": "Không ghi vào BigQuery (chỉ đọc), không sửa dữ liệu "
                                   "vận hành của phòng ban khác."},
    {"id": "hr_personal", "reason": "Không trả lời thông tin đời tư, lương, đánh giá "
                                    "nhân sự."},
]


def a2a_howto() -> dict:
    """Hướng dẫn agent khác hỏi Jenny."""
    return {
        "transport": "lark_im",
        "address": {
            "open_id": JENNY_OPEN_ID,
            "display_name": "Jenny - BOD Assistant",
            "note": "Jenny chạy bằng TÀI KHOẢN NGƯỜI DÙNG Lark, không phải bot app — "
                    "nên tag/nhắn y như nhắn một đồng nghiệp.",
        },
        "how_to_ask": [
            "Cách 1 — nhắn riêng: mở chat 1-1 với open_id trên rồi gửi câu hỏi. "
            "Chat của agent phải nằm trong danh sách cho phép (xem `access` bên dưới).",
            "Cách 2 — trong group: tag Jenny bằng <at user_id=\"" + JENNY_OPEN_ID
            + "\"></at> kèm câu hỏi. Trong group Jenny CHỈ trả lời khi được tag.",
        ],
        "request_format": {
            "language": "Tiếng Việt (tiếng Anh cũng hiểu được)",
            "must_be_self_contained": True,
            "note": "Jenny KHÔNG thấy hội thoại phía bạn. Nêu đủ: cần gì · brand/phòng "
                    "ban nào · khoảng thời gian · muốn số liệu thô hay đã diễn giải.",
            "good_example": "Cho mình doanh thu thuần theo đơn tạo của Mate Made VN "
                            "từ 01/08 đến hôm qua, theo từng ngày, kèm % so với target.",
            "bad_example": "Số liệu hôm qua thế nào?",
        },
        "response": {
            "channel": "Trả lời ngay trong chat/thread mà bạn hỏi",
            "latency_sec": "10-90 (câu cần query BigQuery thì lâu hơn)",
            "on_missing_data": "Jenny nói rõ là không có dữ liệu và đã tra bằng từ khoá "
                               "nào — không bao giờ bịa số.",
        },
        "access": {
            "config_key": "a2a_allowed_agents",
            "note": "Chủ Jenny thêm open_id của agent bạn vào config này để chat được "
                    "duyệt tự động. Chưa có trong danh sách thì Jenny sẽ trả lời là "
                    "chat chưa được duyệt.",
        },
        "etiquette": [
            "Mỗi lượt hỏi một việc, chờ trả lời rồi hãy hỏi tiếp — đừng gửi dồn.",
            "Không dùng Jenny để lấy dữ liệu rồi trình bày như của mình; ghi rõ nguồn.",
            "Nếu Jenny trả lời 'không có dữ liệu', đừng hỏi lại y nguyên — hãy đổi cách "
            "nêu chỉ số hoặc khoảng thời gian.",
        ],
    }


def allowed_agents() -> list[str]:
    """open_id các agent được phép hỏi Jenny (config `a2a_allowed_agents`)."""
    cfg = db.all_configs().get("a2a_allowed_agents", {}) or {}
    return [str(i) for i in (cfg.get("open_ids") or [])]


def manifest() -> dict:
    """Agent Card — bản khai máy đọc được."""
    return {
        "schema": "lsr.agent-card/v1",
        "agent_id": AGENT_ID,
        "name": "Jenny - LSR BOD Assistant",
        "version": VERSION,
        "owner": "thint@hapas.vn",
        "squad": "RETAIL",
        "description": "Trợ lý AI của Ban điều hành Lamson Retail: tra số liệu kinh "
                       "doanh, giám sát bất thường, giao việc & đôn đốc, sổ quyết định, "
                       "đọc tài liệu Lark, tổng hợp thị trường.",
        "language": ["vi", "en"],
        "capabilities": CAPABILITIES,
        "not_supported": NOT_SUPPORTED,
        "a2a": a2a_howto(),
        "peers": {k: {"role": v.get("role")}
                  for k, v in (db.all_configs().get("peer_agents", {}) or {}).items()},
        "generated_at": dt.datetime.now(VN).isoformat(),
    }


def as_markdown() -> str:
    """Bản chữ cho người đọc (và cho agent nào chỉ đọc được text)."""
    m = manifest()
    out = [f"# {m['name']} — bản khai năng lực",
           f"`agent_id: {m['agent_id']}` · phiên bản {m['version']} · chủ sở hữu {m['owner']}",
           "", m["description"], "", "## Làm được gì", ""]
    for c in CAPABILITIES:
        out += [f"### {c['name']}  `{c['id']}`",
                c["description"],
                f"- **Cần cung cấp**: {c['inputs']}",
                f"- **Trả về**: {c['outputs']}",
                f"- **Ví dụ câu hỏi**: _{c['example']}_",
                f"- **Giới hạn**: {c['limits']}", ""]
    out += ["## Không làm", ""]
    for n in NOT_SUPPORTED:
        out.append(f"- **{n['id']}** — {n['reason']}")
    a = m["a2a"]
    out += ["", "## Cách agent khác hỏi Jenny (A2A)", "",
            f"- **Kênh**: Lark IM · **địa chỉ**: `{a['address']['open_id']}` "
            f"({a['address']['display_name']})",
            f"- {a['address']['note']}", ""]
    for h in a["how_to_ask"]:
        out.append(f"- {h}")
    out += ["", "### Cách đặt câu hỏi", "",
            f"- Ngôn ngữ: {a['request_format']['language']}",
            f"- {a['request_format']['note']}",
            f"- ✅ Nên: _{a['request_format']['good_example']}_",
            f"- ❌ Tránh: _{a['request_format']['bad_example']}_",
            "", "### Trả lời", "",
            f"- {a['response']['channel']} · thường {a['response']['latency_sec']} giây",
            f"- {a['response']['on_missing_data']}",
            "", "### Xin quyền truy cập", "", f"- {a['access']['note']}",
            "", "### Nguyên tắc lịch sự", ""]
    for e in a["etiquette"]:
        out.append(f"- {e}")
    if m["peers"]:
        out += ["", "## Agent Jenny sẽ hỏi lại khi cần", ""]
        for k, v in m["peers"].items():
            out.append(f"- **{k}** — {v.get('role')}")
    out += ["", f"_Cập nhật {m['generated_at'][:19]}_"]
    return "\n".join(out)


def announce() -> bool:
    """Đẩy bản khai năng lực về platform qua kênh trace (đường duy nhất có xác thực)."""
    from . import lsr_telemetry

    if not lsr_telemetry.enabled:
        log.info("Không announce năng lực: telemetry đang tắt")
        return False
    m = manifest()
    lsr_telemetry.send(
        run_id=f"capability-announce-{dt.datetime.now(VN).strftime('%Y%m%d')}",
        source="capability_announce",
        final_output=f"Jenny khai báo {len(CAPABILITIES)} năng lực + hướng dẫn A2A",
        extra={"agent_card": m,
               "capability_ids": [c["id"] for c in CAPABILITIES]})
    log.info("Đã announce %d năng lực về LSR platform", len(CAPABILITIES))
    return True


def maybe_announce() -> None:
    """Announce lại mỗi 24h (gọi từ scheduler)."""
    last = db.all_configs().get("capability_announce", {}).get("last", "")
    if last:
        try:
            if dt.datetime.now(VN) - dt.datetime.fromisoformat(last) < dt.timedelta(hours=24):
                return
        except Exception:
            pass
    if announce():
        db.sb().table("configs").upsert({
            "key": "capability_announce",
            "value": {"last": dt.datetime.now(VN).isoformat(),
                      "count": len(CAPABILITIES), "version": VERSION},
            "description": "Lần gần nhất Jenny khai năng lực về LSR platform",
        }, on_conflict="key").execute()
