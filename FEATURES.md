# Jenny — LSR BOD Assistant · Mô tả tính năng tổng thể

> Cập nhật: 2026-08-12 · Trạng thái: **đang vận hành** (Phase 0–3 hoàn chỉnh + nhiều mở rộng ngoài plan)
> Tài liệu này mô tả **chi tiết những gì Jenny đang làm được**, đối chiếu với kế hoạch gốc ([PLAN.md](PLAN.md)) và ghi rõ những thay đổi so với plan.
>
> Sắp làm: **Đợt 1 & Đợt 2** — kế hoạch chi tiết ở [PLAN.md mục 10–11](PLAN.md); cơ sở lựa chọn ở [RESEARCH.md](RESEARCH.md). Tóm tắt ở mục 10 bên dưới.

---

## 1. Jenny là gì

Trợ lý AI cho Ban điều hành (BOD) công ty **LSR (Lamson Retail)**. Jenny hoạt động trực tiếp trong **Lark** (và Telegram), trả lời khi được tag/nhắn riêng, và tự chủ nhiều luồng nghiệp vụ: query số liệu kinh doanh, quản lý task & lịch, ghi biên bản họp, giao việc thay BOD, báo cáo định kỳ, và tra cứu tri thức nội bộ.

**Nguyên tắc cốt lõi (giữ đúng theo plan):**
- **Skill = mô tả năng lực chung** (file `.md` trong bảng `skills`), **chi tiết hay thay đổi = `configs`** trên Supabase → sửa hành vi không cần deploy lại.
- **Trung thực tuyệt đối**: không có dữ liệu thì nói rõ, **không bịa số liệu/nguồn**.
- Tiếng Việt, xưng "em" – gọi "anh/chị"; ngắn gọn, đi thẳng số liệu.

---

## 2. Kiến trúc thực tế (đã tiến hóa so với plan)

```
   Telegram (long-polling)          Lark (2 đường)
        │                    ┌──────────────┴───────────────┐
        │                    │                              │
        ▼                    ▼                              ▼
  jenny (service)     jenny-lark (service)          jenny-events (service)
  telegram_bot.py     lark_user_bot.py              events.py
  tag → trả lời       ⭐ chạy bằng TÀI KHOẢN         listener tenant token:
                      NGƯỜI DÙNG của Jenny           VC recording, task event
                      (OAuth polling, không webhook)
        │                    │                              │
        └────────────────────┼──────────────────────────────┘
                             ▼
            ┌─────────────────────────────────────┐
            │  agent.py — Claude Agent SDK        │
            │  · nạp persona + skills + configs   │
            │  · 2 MCP in-process: bq, lark       │
            │  · tools: BigQuery, Lark API, web   │
            └───────────────┬─────────────────────┘
                            ▼
     ┌──────────────┐  ┌──────────────────┐  ┌────────────────────┐
     │  Supabase    │  │ jenny-cron       │  │ jenny-web (FastAPI)│
     │ (state/log)  │  │ scheduler.py     │  │ OAuth Lark +       │
     │              │  │ báo cáo định kỳ, │  │ webhook meeting    │
     │              │  │ org sync, watch  │  │ notes (Circleback) │
     └──────────────┘  └──────────────────┘  └────────────────────┘
                            ▼
                    Dashboard Next.js (Vercel)
```

**5 systemd service trên VPS Hostinger (`187.77.135.158`, `/opt/jenny`):**

| Service | Vai trò |
|---|---|
| `jenny` | Gateway Telegram (long polling) |
| `jenny-lark` | Gateway Lark chạy bằng **tài khoản người dùng** Jenny (polling) — đường chính |
| `jenny-cron` | Scheduler: chạy lịch định kỳ + org sync + theo dõi comment/họp |
| `jenny-events` | Nghe event tenant token (bản ghi VC, event task) mà user token không lấy được |
| `jenny-web` | FastAPI: đăng nhập OAuth Lark + webhook nhận meeting notes ngoài |

### Thay đổi lớn so với plan
- **Lark chuyển từ "bot WebSocket" → "tài khoản người dùng thật" (OAuth polling).** Lý do: token người dùng cho Jenny truy cập **lịch, tài liệu, task, danh bạ** với đúng quyền của một nhân sự — điều bot token không làm được. File `lark_bot.py` (bot cũ) vẫn giữ, nay chỉ dùng cho `jenny-events` (nghe event tenant).
- **Bộ nhớ `.md` chuyển sang Lark Drive** (`lark_memory.py`) thay vì Google Drive như plan. Vì tài khoản Lark đã sẵn xác thực. Backend Google Drive (`drive.py`) vẫn còn như phương án thay thế khi có service account.
- Nhiều **module ngoài plan** đã được xây (meeting notes, NotebookLM, org directory, resources index, doc-comment watch, BOD delegation, self-service scheduling) — xem mục 4.

---

## 3. Tương tác & kênh chat

- **Telegram**: add Jenny vào group → tag/mention/reply hoặc gọi tên "jenny" thì trả lời. Chat riêng trả lời mọi tin. Admin gõ `/approve` để duyệt (whitelist) chat.
- **Lark (đường chính)**: chạy bằng tài khoản người dùng Jenny:
  - **Group**: chỉ trả lời khi được **@mention** hoặc gọi tên trigger.
  - **Chat riêng (p2p)**: người trong config `lark_p2p_partners` được whitelist tự động; Jenny chủ động nhắn chào để mở chat.
  - **Thread (topic reply)**: Jenny **đọc và trả lời ngay trong thread** — tag Jenny trong 1 thread thì trả lời đúng luồng đó. Danh sách thread đang theo dõi được **lưu bền qua Supabase** (`lark_known_threads`), restart không mất dấu.
  - **"Typing indicator"**: gửi tin placeholder "⏳ Em đang xử lý…" rồi **sửa (edit) thành câu trả lời** khi xong.
  - **Chống lặp tuyệt đối**: mỗi `message_id` chỉ xử lý 1 lần (dedup có giới hạn bộ nhớ), cursor luôn tiến — tránh trả lời trùng.
- **Ngữ cảnh người hỏi**: mỗi tin nhắn Jenny tự đính kèm hồ sơ người gửi (chức danh, phòng ban, ghi chú đã học), cờ **thành viên BOD**, và **các việc đang được giao** cho người đó → điều chỉnh câu trả lời theo vai trò.

---

## 4. Các nhóm tính năng chi tiết

### 4.1. Query số liệu kinh doanh — BigQuery *(theo plan)*
- **`get_data_dictionary`**: đọc data dictionary (định nghĩa bảng/cột) từ **wiki Lark** khai trong config `bq_data_dictionary`. Trả mục lục → đọc theo `section` hoặc lọc theo `keyword`. **Luôn đọc trước khi viết SQL.**
- **`bq_query`**: chạy `SELECT`/`WITH` (read-only). Chặn mọi DML/DDL, giới hạn **200 dòng** và **quét tối đa 20GB**. Diễn giải kết quả theo ngôn ngữ kinh doanh, không trả bảng thô.
- **`bq_recent_queries`**: lấy lại các câu SQL đã chạy thành công gần đây (từ lịch sử `tool_calls`) để **tái sử dụng đúng query cũ** thay vì viết lại — dùng khi "dùng lại query trước", "cập nhật lại số đó".

### 4.2. Tài liệu & tri thức nội bộ Lark *(theo plan)*
- **`read_lark_document`**: đọc nội dung wiki/docx/base bằng quyền tài khoản Jenny (không dùng WebFetch với link Lark — sẽ bị chặn đăng nhập).
- Skill `internal-knowledge`: trả lời theo tài liệu khai trong config `internal_docs`; chưa có thì nói rõ, không suy đoán.

### 4.3. Lịch làm việc — Lark Calendar *(theo plan)*
- **`calendar_list_events`** / **`calendar_create_event`** / **`calendar_delete_event`**: xem/tạo/hủy sự kiện trên lịch Jenny (luôn xác nhận trước khi tạo/hủy). Giờ nhập/hiển thị đều là giờ VN.

### 4.4. Quản lý task — Lark Task *(theo plan)*
- **`task_create`** (giao cho người khác hoặc Jenny tự theo dõi, có deadline), **`task_list`**, **`task_complete`**. Dùng **tenant token** (task API không cấp cho user token).

### 4.5. Giao việc thay BOD — Assignments ⭐ *(ngoài plan)*
Luồng giao việc chuẩn hóa, **chỉ thành viên BOD** mới giao được. Bắt buộc đủ **4 yếu tố**: việc gì (`title`), bối cảnh (`context`), đầu ra cụ thể để chấm (`expected_outcome`), người phụ trách (`pic`).
- **`assignment_create`**: tạo record + tạo Lark task + **tự nhắn PIC** đầy đủ thông tin.
- **`assignment_list`**: việc đang mở (assigned / in_review / needs_more).
- **`assignment_update`**: cập nhật trạng thái; khi `done` bắt buộc kèm **bản tổng hợp cho BOD** và tự complete Lark task.
- **`assignment_remind`** / **`assignment_notify_assigner`**: đôn đốc PIC / báo cáo & escalate lên người giao.

### 4.6. Biên bản họp tự động — Meeting Notes ⭐ *(ngoài plan)*
Pipeline đầy đủ (`meetings.py` + `events.py` + `transcribe.py` + `minutes_web.py`):
1. **Theo dõi lịch Jenny**, tự **RSVP** lời mời họp theo danh sách được phép (`meeting_authorized_ids`) — **chỉ xử lý cuộc họp có mời Jenny**.
2. Nhận **bản ghi**: qua event VC (`jenny-events`), file audio gửi trong chat, hoặc link Lark Minutes.
3. **Gỡ băng** qua Whisper server nội bộ (large-v3); nếu Lark chặn API export thì fallback **đọc transcript bằng trình duyệt headless** (`minutes_web.py`).
4. Claude soạn **draft notes (.md)** → **`meeting_save_draft`** → gửi người tạo họp **duyệt**.
5. **`meeting_finalize`**: lưu kho bộ nhớ, gửi notes cho toàn bộ người dự, ghi nhận số task đã tạo từ action item.
- **`meeting_list_pending`**: các cuộc họp đang chờ nội dung/duyệt.
- Ngoài ra `jenny-web` nhận **meeting notes từ ngoài** (Circleback / Lark Meeting Agent) qua webhook → chuẩn hóa `.md` vào kho.

### 4.7. Danh bạ tổ chức — Org Directory ⭐ *(ngoài plan)*
- Đồng bộ từ **Lark Contacts** hằng ngày → bảng `people` + sinh **sơ đồ tổ chức `.md`** trong kho bộ nhớ.
- **`org_lookup`**: tìm người theo tên/chức danh/phòng ban ("cung ứng", "kế toán trưởng"…) → trả tên, chức danh, phòng ban, ghi chú đã học, open_id.
- **`person_note_save`**: lưu **ghi chú công việc** ngắn về một người (dự án, mảng phụ trách) để lần sau trả lời đúng ngữ cảnh — không lưu đời tư/đánh giá cá nhân.

### 4.8. Danh bạ tài nguyên — Resources Index ⭐ *(ngoài plan)*
- Jenny **tự index mọi link/file/tài liệu** được share trong chat (âm thầm, không trả lời). Với link Lark doc còn đọc nhanh tiêu đề + trích đoạn.
- **`search_resources`** (tìm theo từ khóa) / **`recent_resources`** (gần đây nhất).

### 4.9. Theo dõi & trả lời comment tài liệu ⭐ *(ngoài plan)*
- **`watch_document`**: thêm 1 tài liệu Lark (docs/base/wiki) vào danh sách theo dõi. Sau đó ai **tag Jenny trong comment** của tài liệu đó (hoặc trong **comment của task**) sẽ được **trả lời tự động** (quét mỗi 2 phút).

### 4.10. Tri thức sâu — NotebookLM ⭐ *(ngoài plan)*
- **`notebooklm_ask`**: hỏi đáp có trích dẫn dựa trên notebook tri thức LSR (meeting notes, báo cáo, tài liệu đã đồng bộ).
- **`notebooklm_add_source`**: thêm nguồn (URL hoặc text markdown).
- **`notebooklm_audio_overview`**: tạo **podcast 2 giọng** tiếng Việt từ notebook rồi gửi file vào chat (chạy nền 5–15 phút).
- Mỗi lần `memory_save` cũng tự đẩy nội dung vào notebook.

### 4.11. Bộ nhớ dài hạn — Memory `.md` *(theo plan, đổi backend)*
Kho `.md` trên **Lark Drive** (`Jenny-BOD-Memory/` + thư mục con `meetings/reports/market/knowledge/summaries` + INDEX).
- **`memory_save`** (lưu + cập nhật INDEX), **`memory_index`** (đọc mục lục), **`memory_read`** (đọc 1 file theo token). Quy tắc **tra cứu 2 bước**: đọc INDEX → chọn file → mới đọc, để tiết kiệm token.

### 4.12. Báo cáo định kỳ & tự đặt lịch — Scheduler *(theo plan + mở rộng)*
- `jenny-cron` chạy lịch trong bảng `scheduled_tasks` theo cron (giờ VN), gửi kết quả vào Lark/Telegram; hỗ trợ **trả vào đúng thread** (`chat_id#message_id`). Có **retry 1 lần** sau 60s, báo lỗi vào chat nếu thất bại.
- **`schedule_create`** ⭐ *(ngoài plan)*: Jenny **tự đặt lịch định kỳ** từ hội thoại ("cập nhật doanh thu mỗi tiếng"). Hỗ trợ `thread_reply_to` (cập nhật vào đúng thread) và `until` (**hạn dừng** — "cập nhật đến 9pm hôm nay"). Đặt xong **chạy ngay 1 lần**, không chờ chu kỳ đầu.
- **`schedule_list`** / **`schedule_delete`** (xóa hoặc chỉ tắt).
- **Hạn dừng `expires_at`** ⭐: scheduler tự tắt lịch khi quá hạn.

### 4.13. Nghiên cứu thị trường & web *(theo plan)*
- Skill `web-research`: `WebSearch` + `WebFetch`, luôn ghi nguồn, nêu số liệu kèm thời điểm, nói rõ độ tin cậy.

### 4.14. Tin nhắn thoại — nói với Jenny thay vì gõ ⭐ *(Đợt 1)*
- Gửi **voice note** trong Lark → Jenny gỡ băng (Whisper) rồi xử lý y như tin chữ.
- Jenny **chép lại nội dung nghe được** (`🎤 Em nghe: «…»`) ở đầu câu trả lời để phát hiện ngay nếu nghe sai.
- **Lọc chi phí**: chat riêng luôn xử lý; trong group chỉ xử lý khi tin nằm trong thread Jenny theo dõi hoặc là reply. Tin thoại dài quá ngưỡng (mặc định 5 phút) được coi là bản ghi họp, chuyển sang pipeline meeting.
- Config `voice_note`: `enabled`, `max_duration_sec`, `group_requires_reply`, `echo_transcript`.

### 4.15. Cảnh báo bất thường số liệu ⭐ *(Đợt 1)*
Nguyên tắc: **thống kê phát hiện, LLM chỉ diễn giải** — không để model tự "ngửi" bất thường.
- Baseline so với **cùng thứ trong tuần** của tối đa 8 tuần gần nhất, dùng **median + MAD** để ngày campaign không kéo lệch.
- **Sàn nhiễu 5%**: giả định chỉ số luôn dao động tự nhiên ít nhất 5% — chống báo động giả khi chuỗi quá phẳng.
- **Blackout** ngày campaign/lễ (8/8, 9/9, 11/11, Tết…) và **cooldown** theo từng phép giám sát.
- **Phân tầng**: mức cao → nhắn ngay · trung bình → gom vào brief/digest · thấp → chỉ ghi log.
- Diễn giải do agent viết (được phép chạy `bq_query` bóc tách nguyên nhân), kết bằng 1 khuyến nghị.
- Tools: `monitor_create` / `monitor_list` / `monitor_delete` · `anomaly_recent`.

### 4.16. Tự động đôn đốc việc đã giao ⭐ *(Đợt 1)*
- Nhắc PIC khi **còn 24h tới hạn**, và khi **quá hạn** (mỗi 24h, tối đa 3 lần).
- **Gộp mọi việc của cùng một người vào MỘT tin** — không spam nhiều tin rời.
- Quá ngưỡng → **escalate lên người giao** kèm 3 lựa chọn: gia hạn / đổi người / hủy.
- Tôn trọng **giờ im lặng** 21:00–07:00.
- **Tỷ lệ hoàn thành cam kết** (`assignment_stats`): % việc đến hạn được đóng đúng hạn — đưa lên đầu tổng kết cuối ngày và digest đầu tuần.
- Config `assignment_chase`.

### 4.17. Sổ quyết định & tự đo kết quả ⭐ *(Đợt 2)*
Vòng lặp không công cụ nào trên thị trường làm đủ:
- **Ghi quyết định** kèm kỳ vọng **đo được** (metric + mục tiêu), mức tự tin, mốc nhìn lại, và câu SQL để đo (`decision_log`).
- Phân loại theo **cửa 1 chiều / 2 chiều**: đảo ngược được thì khuyên quyết nhanh; không đảo ngược được mới cần brief + pre-mortem.
- **Đến hạn Jenny tự chạy lại SQL**, đối chiếu kỳ vọng vs thực tế, viết bài học, lưu vào sổ. Không đo tự động được thì hỏi thẳng người quyết.
- **Calibration**: tích lũy để so mức tự tin đã khai với tỷ lệ đạt thực tế (bật khi đủ ≥6 tháng dữ liệu).
- Tools: `decision_log` / `decision_list` / `decision_update`.
- Skill `decision-support`: mẫu **decision brief 7 phần** (kiểu 6-pager), **pre-mortem**, red-team tự phản biện.

### 4.18. Brief sáng dạng âm thanh ⭐ *(Đợt 2)*
- Lịch nào được liệt kê trong config `audio_brief.schedule_ids` sẽ có thêm **bản đọc thành tiếng** gửi vào chat.
- Bản đọc được **viết lại riêng cho tai nghe** (bỏ markdown/bảng, số làm tròn, câu ngắn, 2–3 phút) chứ không TTS thẳng bản chữ.
- ElevenLabs multilingual; cần `ELEVENLABS_API_KEY`. Chưa có key thì tự tắt, bản chữ vẫn gửi bình thường.

---

## 5. Bảng tra cứu công cụ (MCP tools)

**Server `bq`** (`bq_tools.py`): `get_data_dictionary`, `bq_query`, `bq_recent_queries`, `anomaly_recent` ⭐.

**Server `lark`** (`lark_tools.py`):

| Nhóm | Tools |
|---|---|
| Tài liệu | `read_lark_document` |
| Lịch | `calendar_list_events`, `calendar_create_event`, `calendar_delete_event` |
| Task | `task_create`, `task_list`, `task_complete` |
| Tin nhắn | `send_lark_message` |
| Họp | `meeting_list_pending`, `meeting_save_draft`, `meeting_finalize` |
| Tổ chức | `org_lookup`, `person_note_save` |
| Tài nguyên | `search_resources`, `recent_resources`, `watch_document` |
| Giao việc BOD | `assignment_create`, `assignment_list`, `assignment_update`, `assignment_remind`, `assignment_notify_assigner` |
| Lịch định kỳ | `schedule_create`, `schedule_list`, `schedule_delete` |
| Chỉ số cam kết ⭐ | `assignment_stats` |
| Sổ quyết định ⭐ | `decision_log`, `decision_list`, `decision_update` |
| Giám sát số liệu ⭐ | `monitor_create`, `monitor_list`, `monitor_delete` |
| NotebookLM | `notebooklm_ask`, `notebooklm_add_source`, `notebooklm_audio_overview` |
| Bộ nhớ | `memory_save`, `memory_index`, `memory_read` |

Tool web (SDK): `WebSearch`, `WebFetch`. **Bị cấm trên VPS**: `Bash`, `Write`, `Edit`, `NotebookEdit`, `Task` (Jenny không sửa file/chạy shell trên server).

---

## 6. Mô hình dữ liệu (Supabase)

| Bảng | Nội dung | Migration |
|---|---|---|
| `conversations` | chat/group theo kênh + cờ whitelist | 0001 |
| `messages` | tin vào/ra + token | 0001 |
| `tool_calls` | nhật ký tool (tên, tham số, trạng thái, thời gian) | 0001 |
| `token_usage` | token theo ngày/phiên/model | 0001 |
| `skills` | skill (.md, enabled, version) — CRUD từ dashboard | 0001 |
| `configs` | key/value JSONB — sửa hành vi không cần deploy | 0001 |
| `scheduled_tasks` | lịch cron + kênh gửi (+ `expires_at`) | 0001 / 0007 |
| `agents`, `agent_messages` | registry + hàng đợi A2A (đã dựng, **chưa kích hoạt**) | 0001 |
| `resources` ⭐ | danh bạ link/file/doc share trong chat | 0003 |
| `people` ⭐ | danh bạ nhân sự sync từ Lark Contacts + ghi chú học | 0004 |
| `meetings` ⭐ | theo dõi biên bản họp (trạng thái, notes_md, file) | 0005 |
| `assignments` ⭐ | việc BOD giao (4 yếu tố, trạng thái, kết quả) + đếm nhắc/escalate | 0006 / 0009 |
| `monitors` ⭐ | phép giám sát số liệu (anomaly + signpost) | 0008 |
| `anomaly_events` ⭐ | lịch sử bất thường đã phát hiện + diễn giải | 0008 |
| `decisions` ⭐ | sổ quyết định: kỳ vọng đo được → kết quả thực tế | 0010 |

*(RLS bật toàn bộ, không policy → chỉ service key trên VPS truy cập; dashboard dùng auth riêng.)*

---

## 7. Cấu hình chạy-thời-gian (bảng `configs`)

Sửa các key này trên dashboard/Supabase là đổi hành vi **không cần deploy**:

`persona`, `company_instructions`, `reply_rules` (trigger_names…), `bq_data_dictionary` (project_id + link wiki), `internal_docs`, `drive_memory_folder` / `lark_memory`, `notebooklm`, `transcribe_server`, `lark_admin_ids`, `lark_p2p_partners`, `lark_p2p_map`, `lark_known_threads`, `bod_members`, `meeting_authorized_ids`, `org_sync` / `org_chart_file`, `doc_comment_cursor`, `lark_user_token`.

Đợt 1–2 bổ sung: `voice_note` ⭐ · `anomaly_defaults` ⭐ (giờ im lặng, ngày blackout, sàn nhiễu) · `assignment_chase` ⭐ · `tts` ⭐ · `audio_brief` ⭐ (danh sách lịch cần đọc thành tiếng).

**Skills đang có**: `web-research` ✅, `bigquery-analytics` ✅, `internal-knowledge` ✅, `memory`, `decision-support` ⭐, `proactive-monitoring` ⭐ (kèm các skill nghiệp vụ bổ sung trên Supabase). Thêm/bật/tắt skill từ dashboard → Jenny nạp lại ở phiên mới.

---

## 8. Tóm tắt: đã thay đổi gì so với PLAN.md

**Giữ đúng plan:** kiến trúc SDK + Supabase + dashboard; skill=general / config=chi tiết; BigQuery read-only; Telegram long-polling; báo cáo định kỳ; bộ nhớ `.md` 2 tầng (log ở Supabase, nội dung ở Drive).

**Khác / mở rộng so với plan:**
1. **Lark = tài khoản người dùng (OAuth polling)** thay vì bot WebSocket → có quyền lịch/doc/task/danh bạ thật. Bot cũ chỉ còn để nghe event.
2. **Bộ nhớ chuyển sang Lark Drive** (Google Drive thành phương án phụ).
3. **Meeting notes pipeline** hoàn chỉnh: Whisper, auto-RSVP, fallback headless, duyệt → phát hành.
4. **NotebookLM**: hỏi đáp trích dẫn + thêm nguồn + audio overview.
5. **Org directory** sync Lark Contacts + ghi chú học về người.
6. **Resources index** tự động + tìm kiếm.
7. **Doc/comment watch**: trả lời khi bị tag trong comment tài liệu/task.
8. **BOD delegation (assignments)**: giao việc chuẩn hóa 4 yếu tố, đôn đốc, chấm, tổng hợp.
9. **Self-service scheduling**: Jenny tự đặt/sửa/xóa lịch định kỳ từ chat, có **hạn dừng** và **chạy ngay**.
10. **Hỗ trợ thread Lark** + **lưu bền danh sách thread** + **chống lặp theo message_id**.
11. **`jenny-events`**: nghe event tenant token (VC recording, task).
12. **Tích hợp LSR agent platform** (nạp `.env.lsr`, hooks telemetry).
13. **Zalo & multi-agent (A2A): chưa làm** — bảng `agents`/`agent_messages` đã dựng sẵn nhưng chưa kích hoạt; Zalo/Messenger để phase sau.

---

## 9. Bảo mật & vận hành

- Toàn bộ secrets ở `config/secrets/` (đã gitignore). Service key Supabase chỉ nằm trên VPS.
- Jenny **không có Bash/Write/Edit** trên server; BigQuery **read-only**; chỉ trả lời chat đã whitelist.
- Đăng nhập Lark của Jenny qua OAuth (`/lark/oauth/start/<secret>` trên `jenny-web`); token tự refresh, lưu ở config `lark_user_token`.
- Log vận hành đầy đủ (messages / tool_calls / token_usage) phục vụ dashboard theo dõi chi phí.

---

## 10. Trạng thái Đợt 1 & Đợt 2

> Kế hoạch chi tiết: [PLAN.md mục 10–11](PLAN.md) · Cơ sở lựa chọn: [RESEARCH.md](RESEARCH.md)

| # | Tính năng | Trạng thái |
|---|---|---|
| 10.1 | Voice note trong Lark | ✅ đã code + deploy |
| 10.2 | Cảnh báo bất thường số liệu | ✅ đã code + deploy · cần chạy migration `0008` |
| 10.3 | Digest thứ 2 + tỷ lệ hoàn thành cam kết | ✅ tool + skill xong · **cần tạo lịch digest** |
| 10.4 | Tự động đôn đốc việc đã giao | ✅ đã code + deploy · cần migration `0009` |
| 11.1 | Sổ quyết định + tự đo kết quả | ✅ đã code + deploy · cần migration `0010` |
| 11.2 | Decision brief + pre-mortem | ✅ skill `decision-support` · cần migration `0011` |
| 11.3 | Brief sáng dạng âm thanh | ✅ đã code · **cần `ELEVENLABS_API_KEY`** rồi bật trong `audio_brief` |
| 11.4 | Thẻ tương tác 1 chạm | ⏸ chờ spike kỹ thuật (Lark có thể chặn card từ user token) |
| 11.5 | Ngoại lệ S&OP + signpost | 🟡 hạ tầng signpost xong (`monitors.kind='signpost'`) · còn phần S&OP |

**Việc cần làm để chạy đầy đủ**: chạy 4 migration `0008`–`0011` trên Supabase SQL Editor (kèm `NOTIFY pgrst, 'reload schema';`), tạo phép giám sát đầu tiên bằng `monitor_create`, tạo lịch digest thứ 2, và thêm `ELEVENLABS_API_KEY` nếu muốn bản audio.

### Đợt 3 — Thiết bị *(pilot nhỏ, sau Đợt 1–2)*
Máy ghi âm **Plaud NotePin** cho họp offline (test chất lượng nghe tiếng Việt trước) · màn hình **e-ink để bàn** · **podcast riêng tư** nghe qua CarPlay · dashboard TV kèm lời bình.

### Đã thẩm định — **không làm**
Loa thông minh (Alexa for Business đã khai tử, Google/Apple không cho agent ngoài vào) · gọi điện AI · app đồng hồ riêng · nhẫn thông minh trong Jenny · IoT văn phòng · mô phỏng kịch bản đầy đủ (chưa ai ship thật) · giám sát tuân thủ tự động.

### Vẫn hoãn từ plan gốc
- **Phase 4 — Zalo** (cần domain + Zalo OA).
- **Phase 5 — Multi-agent (A2A) + Messenger**: bus `agent_messages` đã dựng sẵn, chưa kích hoạt.

---

## 11. Quy ước tài liệu

> **Khi có thay đổi về kế hoạch hoặc tính năng lớn: cập nhật `PLAN.md` và `FEATURES.md` TRƯỚC, rồi mới commit git.**

- `PLAN.md` — kế hoạch, lộ trình, trạng thái phase, kế hoạch chi tiết đợt sắp làm.
- `FEATURES.md` *(file này)* — tính năng **đang có thật** (đã deploy) + roadmap tóm tắt.
- `RESEARCH.md` — nghiên cứu nền, cơ sở lý do cho lựa chọn tính năng.
- Tài liệu sửa xong commit **cùng commit** với code tính năng đó, để tài liệu không trôi khỏi thực tế.
- Sửa lỗi / tính năng nhỏ: không bắt buộc cập nhật 2 file này.
