# Jenny — LSR BOD Assistant · Kế hoạch tổng thể

> Trạng thái: **ĐANG VẬN HÀNH** — Phase 0–3 xong, Jenny chạy thật trên VPS.
> Đang triển khai: **Đợt 1 & Đợt 2** (mục 10–11) — kế hoạch chi tiết bên dưới.
> Cập nhật: 2026-08-12
>
> Tài liệu liên quan: [FEATURES.md](FEATURES.md) — mô tả tính năng hiện có ·
> [RESEARCH.md](RESEARCH.md) — nghiên cứu thị trường & cơ sở lý do cho Đợt 1–2

## 1. Mục tiêu

Trợ lý AI cho Ban điều hành (BOD) của LSR, hoạt động trong các group chat:
- Được add vào group, **trả lời khi được tag**
- Query số liệu kinh doanh từ **BigQuery** (dựa trên data dictionary)
- Thao tác **Lark**: tin nhắn, lịch, tài liệu, task
- **Quản lý task qua Lark**: tạo/giao task, theo dõi tiến độ, nhắc deadline
- **Quản lý lịch làm việc qua Lark**: xem lịch BOD, đặt/dời lịch họp, agenda ngày
- **Tổng hợp thông tin thị trường** và làm báo cáo
- **Báo cáo định kỳ** tự động (sáng/tuần) vào group chat
- Trả lời **kiến thức nội bộ** LSR từ tài liệu trên cloud
- Tìm kiếm thông tin theo yêu cầu (web search)
- Sẵn sàng **trao đổi với agent khác** trong tương lai (multi-agent)

## 2. Quyết định đã chốt

| Hạng mục | Quyết định |
|---|---|
| Agent framework | **Claude Agent SDK** (Python) — không dùng Hermes |
| Xác thực Claude | **Claude Max/Pro subscription** (đăng nhập Claude Code trên VPS) |
| Hạ tầng agent | VPS Hostinger `187.77.135.158` (thông tin: `config/secrets/hostinger.env`) |
| Database | **Supabase** (tạo project mới, có hướng dẫn chi tiết) |
| Dashboard | **Next.js trên Vercel** |
| Kênh chat | Telegram → Lark → Zalo (Messenger để sau) |
| Agent khác | Chưa có — thiết kế sẵn cơ chế A2A qua Supabase |

## 3. Nguyên tắc thiết kế skill (theo yêu cầu)

- **Skill = general**: mỗi skill là file `.md` mô tả năng lực chung, ví dụ
  *"Khi cần số liệu, query BigQuery. Định nghĩa bảng lấy từ data dictionary tại config `bq_data_dictionary`"*.
- **Chi tiết hay thay đổi = config trên cloud**: data dictionary, mẫu báo cáo,
  danh sách nguồn tin thị trường, quy tắc trả lời… lưu trong bảng `configs`
  (Supabase) hoặc Lark Doc — agent đọc lúc chạy, sửa không cần deploy lại.
- **Skill quản lý từ dashboard**: thêm/xóa/sửa skill trên dashboard → lưu Supabase
  → agent nạp lại khi bắt đầu phiên mới.

## 4. Kiến trúc

```
  Telegram        Lark          Zalo OA        (Messenger - sau)
     │              │              │
     ▼              ▼              ▼
┌─────────────────────────────────────────┐
│  Gateway (FastAPI, VPS Hostinger)       │  ← nhận tin nhắn (polling/webhook)
│  · whitelist BOD · map chat → session   │
└──────────────────┬──────────────────────┘
                   ▼
┌─────────────────────────────────────────┐
│  Jenny Agent Core (Claude Agent SDK)    │
│  · nạp skills + configs từ Supabase     │
│  · tools: BigQuery, Lark API, web       │
│    search, đọc tài liệu nội bộ          │
│  · Scheduler: báo cáo định kỳ (cron)    │
│  · A2A bus (bảng agent_messages)        │
└──────────────────┬──────────────────────┘
                   ▼
┌─────────────────────────────────────────┐
│  Supabase                               │
│  conversations · messages · tool_calls  │
│  token_usage · skills · configs         │
│  scheduled_tasks · agents · agent_msgs  │
└──────────────────┬──────────────────────┘
                   ▼
┌─────────────────────────────────────────┐
│  Dashboard (Next.js / Vercel)           │
│  · theo dõi tool calls, token usage     │
│  · quản lý skills (thêm/xóa/sửa)        │
│  · sửa configs · xem hội thoại · lịch   │
└─────────────────────────────────────────┘
```

### Ghi chú kỹ thuật quan trọng

- **Telegram**: dùng **long polling** cho prototype — không cần domain/HTTPS,
  chạy được ngay trên VPS.
- **Lark**: dùng **WebSocket mode** của SDK `lark-oapi` — cũng không cần
  webhook công khai. Cần quyền admin Lark tạo custom app + bot.
- **Zalo OA**: bắt buộc webhook HTTPS → khi đến phase Zalo cần **1 domain**
  trỏ về VPS (Caddy tự lo TLS). *(Câu hỏi mở #1)*
- **Claude Max trên VPS**: cài Claude Code CLI, đăng nhập bằng tài khoản Max
  (`claude setup-token`). Lưu ý: rate limit dùng chung với việc dùng Claude
  cá nhân; phiên đăng nhập cần gia hạn định kỳ — dashboard sẽ có cảnh báo
  khi token hết hạn.

## 5. Cấu trúc Supabase (phác thảo)

| Bảng | Nội dung |
|---|---|
| `conversations` | kênh (telegram/lark/zalo), chat_id, tên group, trạng thái |
| `messages` | tin nhắn vào/ra, người gửi, timestamp, tokens |
| `tool_calls` | tên tool, tham số, kết quả tóm tắt, thời gian chạy, lỗi |
| `token_usage` | tổng hợp token theo ngày/phiên — nguồn cho dashboard |
| `skills` | name, mô tả, nội dung .md, enabled, version — CRUD từ dashboard |
| `configs` | key/value (jsonb) — data dictionary, mẫu báo cáo, quy tắc… |
| `scheduled_tasks` | cron expression, prompt, group chat đích, enabled |
| `agents` | registry agent (Jenny + agent tương lai) — cho A2A |
| `agent_messages` | hàng đợi tin nhắn giữa các agent (A2A bus) |

## 6. Bộ nhớ & lưu trữ nội dung (tiết kiệm token)

Mục tiêu: **không nhồi mọi thứ vào context mỗi request** — nội dung dài lưu
thành file `.md` trên Google Drive, agent chỉ query đúng phần cần khi có
hướng dẫn.

### Nguyên tắc
- Mọi file, báo cáo, nội dung tổng hợp **chưa có format sẵn → chuẩn hóa thành `.md`**
  và lưu vào thư mục Google Drive của Jenny.
- Mỗi request agent chỉ nạp: system prompt gọn + skills liên quan + configs cần
  thiết. Nội dung lịch sử/tri thức **chỉ được đọc khi yêu cầu có hướng dẫn**
  (ví dụ: *"dựa trên báo cáo thị trường tuần trước"* → tìm và đọc đúng file đó).
- Tra cứu 2 bước để tránh đọc thừa: đọc **`INDEX.md`** (danh mục 1 dòng/file:
  tên, ngày, tóm tắt) → chọn đúng file → mới đọc nội dung file đó.

### Cấu trúc thư mục Drive `Jenny-BOD-Memory/`
```
Jenny-BOD-Memory/
├── INDEX.md          ← danh mục toàn bộ file (agent cập nhật mỗi khi ghi file mới)
├── reports/          ← báo cáo định kỳ đã gửi (bao-cao-YYYY-MM-DD.md)
├── market/           ← tổng hợp thông tin thị trường
├── knowledge/        ← kiến thức nội bộ LSR đã chuẩn hóa .md
└── summaries/        ← tóm tắt hội thoại/cuộc họp, nội dung tổng hợp khác
```

### Kỹ thuật
- **Đã tạo** thư mục trên Drive (2026-08-04, tài khoản `thint311@gmail.com`):
  `Jenny-BOD-Memory` — folder ID `1yNdY2zCc0BOiIxY4p2eQ9w_YAbIeym4y`
  (https://drive.google.com/drive/folders/1yNdY2zCc0BOiIxY4p2eQ9w_YAbIeym4y)
  kèm 4 thư mục con `reports/ market/ knowledge/ summaries/` và `INDEX.md`.
- Agent trên VPS truy cập Drive qua **Google service account** (Drive API),
  thư mục `Jenny-BOD-Memory` share cho email service account với quyền Editor
  (làm ở Phase 0).
- Skill `memory` (general): quy định cách ghi file .md, cập nhật INDEX.md,
  và cách tra cứu 2 bước. Đường dẫn folder ID nằm trong `configs`.
- Supabase vẫn giữ log vận hành (messages, tool_calls, token_usage);
  Drive giữ **nội dung** — hai tầng tách biệt.

## 7. Lộ trình

### Phase 0 — Nền móng ✅ **XONG** (2026-08-04)
- Tạo Supabase project mới (hướng dẫn từng bước) + chạy migration schema
- Setup VPS: SSH key, đổi mật khẩu root, Python, Claude Code CLI + đăng nhập Max
- Repo cấu trúc: `agent/` (VPS) + `web/` (Vercel) + `supabase/migrations/`

### Phase 1 — Prototype ✅ **XONG** (2026-08-04)
- Telegram bot (long polling): add vào group, tag `@jenny` thì trả lời
- Agent core với Claude Agent SDK, nạp skill từ Supabase
- 4 skill đầu: **hỏi đáp + web search**, **query BigQuery** (data dictionary
  trong `configs`), **kiến thức nội bộ** (đọc tài liệu được khai báo),
  **memory** (ghi/tra cứu .md trên Drive theo mục 6)
- Log đầy đủ messages / tool_calls / token_usage vào Supabase
- **→ Demo cho anh/chị duyệt trước khi làm tiếp**

### Phase 2 — Dashboard Vercel ✅ **XONG**
- Trang theo dõi: tool calls, token usage theo ngày, hội thoại gần đây
- Quản lý skills (thêm/xóa/sửa, bật/tắt) và configs
- Quản lý scheduled tasks

### Phase 3 — Lark + báo cáo định kỳ ✅ **XONG** (mở rộng nhiều so với plan — xem FEATURES.md)
- Lark bot (WebSocket), tools thao tác Lark (tin nhắn, lịch, doc, task)
- **Skill quản lý task (Lark Task)**: tạo/giao task từ hội thoại ("giao anh A
  làm X trước thứ 6"), xem danh sách task theo người/deadline, nhắc task
  sắp/quá hạn tự động vào group hoặc chat riêng
- **Skill quản lý lịch làm việc (Lark Calendar)**: xem lịch BOD theo
  ngày/tuần, tìm khung giờ trống chung, đặt/dời/hủy lịch họp, gửi agenda
  đầu ngày cho từng thành viên BOD
- Scheduler chạy báo cáo định kỳ gửi vào group (Telegram/Lark)
- Skill báo cáo thị trường (nguồn tin khai báo trong `configs`)
- Quyền Lark app cần xin: `im:message`, `calendar` (đọc/ghi), `task`
  (đọc/ghi), `docx`, `contact:user.base` — khai trong Lark Developer Console

### Phase 4 — Zalo ⏸ **HOÃN** *(cần domain + Zalo OA; ưu tiên thấp hơn Đợt 1–2)*
- Webhook HTTPS qua Caddy, tích hợp Zalo OA

### Phase 5 — Multi-agent + Messenger ⏸ **CHƯA** *(bảng `agents`/`agent_messages` đã dựng sẵn)*
- Chuẩn hóa A2A bus, thêm agent thứ hai
- Facebook Messenger (cần Meta App review)

## 7b. Meeting notes pipeline (đã dựng 2026-08-04)

```
Circleback (automation webhook) ─┐
                                 ├→ jenny-web (FastAPI, VPS) → chuẩn hóa .md
Lark Meeting Agent (Anycross) ───┘     → Drive Jenny-BOD-Memory/meetings/ + INDEX.md
                                        (chưa có Google SA → lưu tạm /opt/jenny/pending-drive,
                                         có SA thì sync_pending() tự đẩy lên)
```

- HTTPS qua **Traefik có sẵn** trên VPS (file provider `/docker/traefik/dynamic/jenny.yml`),
  domain tạm `jenny-187-77-135-158.nip.io` — có thể đổi sang `jenny.hapas-ai.tech`
  bằng cách thêm DNS A record + sửa 1 dòng config.
- Webhook URL (secret trong path, xem `config/secrets/agent.env`):
  - Circleback: `POST /webhook/circleback/<WEBHOOK_SECRET>`
  - Lark: `POST /webhook/lark-meeting/<WEBHOOK_SECRET>` body `{"doc_url": "..."}`
- Lưu ý VPS: cổng 80/443 do **Traefik** của hệ thống khác quản lý (hapas-ai.tech,
  container lark-claude cũ) — không dùng Caddy, không đụng các route có sẵn.

## 8. Câu hỏi mở (chưa chặn prototype, cần trả lời trước phase liên quan)

1. **Domain** nào trỏ về VPS cho webhook Zalo (và Messenger sau này)?
2. **BigQuery**: project ID + service account, và data dictionary hiện có ở đâu
   (Lark Doc? file?) để đưa vào `configs`?
3. **Whitelist**: những ai/group nào được phép dùng Jenny?
4. **Zalo**: đã có Official Account chưa, hay dùng tài khoản cá nhân
   (rủi ro khóa tài khoản với API không chính thức)?

## 9. Bảo mật

- Toàn bộ secrets nằm trong `config/secrets/` (đã gitignore)
- **Nên đổi mật khẩu root VPS** sau khi setup (đã gửi qua chat), chuyển sang
  SSH key, tắt password login
- Service key Supabase chỉ nằm trên VPS; dashboard dùng RLS + auth riêng
- Agent chỉ trả lời chat/user trong whitelist

---

# ĐỢT 1 & ĐỢT 2 — KẾ HOẠCH CHI TIẾT

> Cơ sở lựa chọn: [RESEARCH.md](RESEARCH.md) (khảo sát thị trường trợ lý board, thiết bị ngoài, decision intelligence — 2026-08-12).
>
> **Trạng thái 2026-08-12: 10.1–10.4 và 11.1–11.2 ĐANG CHẠY THẬT.**
> Migration `0008`–`0011` đã chạy trên Supabase; 2 monitor đầu tiên + lịch digest thứ 2
> đã tạo. Còn lại: 11.4 chờ spike · 11.5 chờ file S&OP đã chốt.
> **11.3 (brief dạng âm thanh): ĐÃ BỎ** theo yêu cầu — code đã gỡ 2026-08-12.
> Bảng trạng thái chi tiết: [FEATURES.md mục 10](FEATURES.md).
>
> **Kết nối Postgres trực tiếp** (để chạy DDL, PostgREST không làm được):
> host `aws-1-ap-northeast-2.pooler.supabase.com:5432`, user `postgres.<project_ref>`,
> mật khẩu ở `config/secrets/supabase.env` (`SUPABASE_DB_PASSWORD`).

## Nguyên tắc xuyên suốt (rút từ nghiên cứu)

1. **Mọi output kết bằng 1 khuyến nghị hoặc 1 câu hỏi** — không bao giờ là đống dữ liệu thô (BCG: thông tin thừa làm quyết định tệ đi).
2. **Thống kê phát hiện, LLM diễn giải** — không dùng LLM để "ngửi" bất thường; LLM chỉ viết phần *vì sao* và *nên làm gì*.
3. **Không làm phiền quá ngưỡng** — mọi luồng chủ động phải có cooldown + giờ im lặng + phân tầng độ khẩn.
4. **Tận dụng hạ tầng sẵn có** — jenny-cron cho việc nền, Supabase cho state, Lark làm hub; không dựng service mới nếu không bắt buộc.

---

## 10. Đợt 1 — Chủ động & thu nhận *(ước lượng ≈ 10–12 ngày công)*

Mục tiêu đợt: Jenny **chủ động lên tiếng đúng lúc** (bất thường số liệu, việc sắp trễ) và **nhận được đầu vào bằng giọng nói**. Toàn bộ là phần mềm, không mua thiết bị.

### 10.1 — Voice note: nói với Jenny thay vì gõ ⭐ ưu tiên cao nhất
*Ước lượng: 2–3 ngày*

**Mục tiêu:** BOD gửi tin nhắn thoại trong Lark → Jenny nghe, hiểu, làm như tin nhắn chữ. Hợp thói quen voice-note của lãnh đạo Việt; dùng được khi đang di chuyển.

**Việc cần làm**
1. `lark_user_bot.py` — `_handle_message`: thêm nhánh xử lý `msg_type == "audio"` (tin nhắn thoại Lark, body chứa `file_key` + `duration`).
2. Tải file: dùng `lark_user.download_message_resource(message_id, file_key)` sẵn có.
3. `transcribe.py` — bổ sung xử lý định dạng **opus** của Lark: chuyển sang wav/mp3 bằng ffmpeg (ffmpeg đã có sẵn trên VPS, đang dùng để nén audio khi gửi file) trước khi đẩy lên Whisper server.
4. **Quy tắc lọc để khỏi tốn tiền transcribe vô ích:**
   - Chat riêng (p2p) → luôn xử lý.
   - Group → chỉ xử lý nếu tin thoại là **reply vào tin của Jenny** hoặc nằm trong **thread Jenny đang theo dõi**. Ngoài ra bỏ qua.
   - Bỏ qua file dài hơn ngưỡng (mặc định 5 phút) → coi là bản ghi họp, đẩy sang pipeline meeting hiện có.
5. **Phản hồi có xác nhận:** Jenny mở đầu câu trả lời bằng *"Em nghe: «…»"* (bản chép lại) rồi mới trả lời — để sếp phát hiện ngay nếu nghe sai.
6. Ghi vào bảng `messages` với nội dung là bản chép + đánh dấu nguồn `voice`.

**Config mới:** `voice_note` = `{enabled, max_duration_sec: 300, group_requires_reply: true, echo_transcript: true}`

**Rủi ro & xử lý**
- *Whisper nghe sai tên riêng/thuật ngữ* → tái dùng cách đã làm ở meeting notes: cho Claude hiệu đính thuật ngữ theo ngữ cảnh trước khi hành động.
- *Sếp nói dài, nhiều ý* → Jenny tách thành danh sách việc và xác nhận lại trước khi thực thi việc có tác động (giao việc, đặt lịch).

**Nghiệm thu**
- Gửi voice note trong chat riêng: *"Cho anh xem doanh thu hôm nay theo brand"* → Jenny chép đúng + trả số liệu.
- Voice note trong group không liên quan Jenny → **không** bị xử lý (kiểm tra log).
- Voice note giao việc → Jenny xác nhận lại trước khi tạo assignment.

---

### 10.2 — Cảnh báo bất thường số liệu (anomaly alert)
*Ước lượng: 4–5 ngày*

**Mục tiêu:** Jenny tự phát hiện số liệu lệch bất thường và báo **kèm diễn giải nguyên nhân**, thay vì chờ có người mở dashboard.

**Thiết kế cốt lõi:** *thống kê phát hiện → LLM diễn giải → đẩy theo tầng độ khẩn.*

**Việc cần làm**
1. **Module mới `agent/jenny/anomaly.py`**
   - `run_monitor(monitor)` — chạy SQL của monitor bằng BigQuery client **trực tiếp** (không qua LLM).
   - `evaluate(series)` — so với **cùng thứ trong tuần của 4 tuần gần nhất** (bán lẻ có mùa vụ theo thứ rất mạnh), dùng **median + MAD** (robust) thay vì trung bình/độ lệch chuẩn để không bị campaign kéo lệch.
   - `severity(z)` — cao / trung bình / thấp theo ngưỡng cấu hình.
2. **Bảng mới** (migration `0008_monitors.sql`):
   - `monitors`: id, name, kind (`anomaly` | `signpost`), sql, metric_label, baseline_method, threshold_high, threshold_med, chat_id, channel, enabled, cooldown_hours, linked_decision_id (dùng cho Đợt 2), created_at.
   - `anomaly_events`: id, monitor_id, ts, value, baseline, z_score, severity, narrative, notified (bool), chat_id, acknowledged_by, created_at.
3. **Hook vào `scheduler.py`**: `anomaly.maybe_check()` mỗi vòng (giống `meetings.maybe_watch()`), tần suất theo cấu hình từng monitor.
4. **Diễn giải bằng LLM**: khi có bất thường → gọi agent với dữ liệu đã có (giá trị, baseline, mức lệch, drill-down theo brand/kênh) để viết 3–5 câu: *chuyện gì → có thể vì sao → nên làm gì*. Không cho LLM tự quyết có bất thường hay không.
5. **Đẩy theo tầng độ khẩn**
   - Cao → nhắn thẳng chat BOD ngay.
   - Trung bình → gom vào Brief sáng / digest thứ 2.
   - Thấp → chỉ ghi `anomaly_events`, không làm phiền.
6. **Chống làm phiền**: cooldown theo monitor (mặc định 12h), giờ im lặng 21:00–07:00 (trừ mức cao), và **danh sách ngày blackout** (Tết, 8/8, 9/9, 11/11, Black Friday) để không báo động giả vào ngày campaign.
7. **Tool mới** `anomaly_recent(days)` — cho Jenny đọc lại lịch sử cảnh báo khi viết brief/digest.
8. Dashboard: trang `/monitors` quản lý monitor (bật/tắt, sửa ngưỡng) — tương tự trang `/schedules` đã có.

**Monitor khởi điểm đề xuất** (chốt lại với BOD khi triển khai)
- Doanh thu thuần theo ngày tạo đơn, theo brand — lệch so với cùng thứ 4 tuần gần nhất.
- Tỷ lệ huỷ/hoàn theo brand.
- Đứt hàng top-seller (tồn kho về dưới X ngày bán).
- Tốc độ tiêu tiền quảng cáo vs doanh thu (MER lệch bất thường).
- ETL trễ / bảng không có dữ liệu mới (đã từng gặp: Thái Lan 0 record).

**Rủi ro & xử lý**
- *Báo động giả ngày campaign* → blackout dates + baseline riêng cho ngày campaign.
- *Chi phí query* → mỗi monitor 1 query gọn, chạy theo lịch riêng (không phải mỗi 30 giây); tận dụng `maximum_bytes_billed` đã có.
- *Alert fatigue* → bắt đầu **chỉ 2–3 monitor**, ngưỡng chặt, nới dần theo phản hồi.

**Nghiệm thu**
- Tạo monitor doanh thu; ngày có biến động thật → nhận cảnh báo kèm diễn giải đúng hướng.
- Cùng monitor không cảnh báo lại trong thời gian cooldown.
- Ngày campaign trong blackout → không cảnh báo.

---

### 10.3 — Digest thứ 2 "Tuần này có gì thay đổi" + tỷ lệ hoàn thành cam kết
*Ước lượng: 2 ngày*

**Mục tiêu:** đầu tuần BOD có 1 bản tin duy nhất trả lời "tuần qua có gì đổi khác và ta cần quyết gì".

**Việc cần làm**
1. **Tool `assignment_stats(period)`** trong `lark_tools.py` + hàm `followthrough_stats()` trong `assignments.py`: tính **tỷ lệ hoàn thành đúng hạn** (% việc đến hạn trong kỳ được đóng đúng hạn), số việc quá hạn, số việc đang chờ.
2. **Lịch mới "Digest thứ 2"** (cron `0 8 * * 1`), prompt chuẩn gồm:
   - Delta KPI tuần này vs tuần trước vs kế hoạch (dùng `bq_recent_queries` để tái dùng query đã chuẩn).
   - Cảnh báo bất thường tuần qua (`anomaly_recent`).
   - Tín hiệu bên ngoài: đối thủ/thị trường (WebSearch) — 2–3 tin có ảnh hưởng.
   - **Mỗi mục bắt buộc kèm một câu "vậy thì sao"** (hàm ý hành động).
   - Kết bằng: 2–3 việc đề xuất BOD quyết tuần này.
3. **Chèn tỷ lệ hoàn thành cam kết** vào đầu lịch "Tổng kết task cuối ngày" và digest thứ 2.
4. Đăng ký tool mới vào `ALLOWED_TOOLS` trong `agent.py`.

**Nghiệm thu:** sáng thứ 2 nhận digest ≤ 500 từ, có đủ 4 phần, mỗi mục có hàm ý hành động, có con số % hoàn thành cam kết.

---

### 10.4 — Tự động đôn đốc việc đã giao (auto-chase)
*Ước lượng: 2 ngày*

**Mục tiêu:** đóng "lỗ rò" kinh điển của quản trị — việc giao xong không ai theo. Đây là tính năng thị trường khen nhiều nhất nhưng hầu hết công cụ bỏ dở.

**Việc cần làm**
1. `assignments.py` — `maybe_chase()` gọi từ vòng lặp `scheduler.py`:
   - Nhắc PIC khi **còn 24h** tới hạn (1 lần).
   - Nhắc khi **quá hạn**, lặp mỗi 24h, **tối đa 3 lần**.
   - **Trước cuộc họp có mặt PIC** (tra lịch): gửi PIC danh sách việc đang mở của họ.
2. **Escalate lên người giao**: quá hạn > 3 lần hoặc PIC không phản hồi trong N ngày → `notify_assigner` kèm đề xuất (gia hạn / đổi người / huỷ việc).
3. **Trước cuộc họp BOD**: gửi vào group danh sách action item kỳ trước + **tỷ lệ hoàn thành** (dùng 10.3).
4. Chống spam: dùng cột `last_reminded_at` sẵn có; giờ im lặng 21:00–07:00; mỗi PIC tối đa 1 tin nhắc/ngày (gộp nhiều việc vào 1 tin).

**Config mới:** `assignment_chase` = `{enabled, before_due_hours: 24, overdue_every_hours: 24, max_overdue_reminders: 3, escalate_after: 3, quiet_hours: [21, 7]}`

**Nghiệm thu**
- Việc sắp đến hạn → PIC nhận đúng 1 nhắc trước 24h.
- Việc quá hạn 3 lần → người giao nhận báo cáo escalate.
- PIC có 3 việc mở → nhận **1 tin gộp**, không phải 3 tin.

---

## 11. Đợt 2 — Vòng lặp quyết định *(ước lượng ≈ 13–18 ngày công)*

Mục tiêu đợt: Jenny sở hữu trọn **vòng lặp quyết định** mà không công cụ nào trên thị trường làm đủ:

```
ghi quyết định (kèm dự đoán) → brief có phương án/rủi ro/khuyến nghị
   → pre-mortem cho quyết định lớn → theo dõi hành động
   → đến hạn tự đo kết quả thật → phản hồi calibration cho người quyết
```

### 11.1 — Sổ quyết định (`decisions`) + tự đo kết quả
*Ước lượng: 3–4 ngày · **nền tảng cho cả Đợt 2***

**Mục tiêu:** mọi quyết định của BOD được ghi lại kèm **kỳ vọng có thể đo được**, và đến hạn Jenny **tự chạy lại số liệu** để đối chiếu kỳ vọng vs thực tế. Con người không bao giờ duy trì nổi sổ này — AI thì có.

**Việc cần làm**
1. **Migration `0009_decisions.sql`** — bảng `decisions`:
   `id, title, decided_at, decider_open_id, decider_name, type (big_bet|cross_cutting|delegated), reversible (bool), context_md, options_md, chosen_option, expected_outcome, expected_metric (jsonb: tên metric, mục tiêu, đơn vị), confidence (int %), review_at, review_sql, actual_outcome, outcome_verdict (đạt|không đạt|một phần), status (open|reviewed|cancelled), source_kind (meeting|chat|assignment), source_id, created_at`
2. **Module `agent/jenny/decisions.py`**: `create/list/get/update`, `maybe_review()` (gọi từ scheduler).
3. **Trích quyết định tự động**: sau `meeting_finalize`, Jenny đề xuất danh sách quyết định phát hiện được trong cuộc họp → **hỏi người chủ trì xác nhận** → mới ghi vào sổ. Không tự ý ghi.
4. **Phân loại khi ghi** (rẻ mà hiệu quả): hỏi đúng 1 câu **"quyết định này đảo ngược được không?"** → cửa 2 chiều thì khuyến nghị quyết nhanh, cửa 1 chiều thì mới áp quy trình nặng (pre-mortem, brief đầy đủ).
5. **Tự đo kết quả**: đến `review_at`, nếu có `review_sql` → chạy lại → đăng vào chat: *"Quyết định D-023 (nâng ngưỡng freeship): kỳ vọng AOV +8%, thực tế +3%, biên lợi nhuận đi ngang"* → lưu `actual_outcome`.
6. **Tools mới**: `decision_log`, `decision_list`, `decision_update`.
7. Dashboard: trang `/decisions` (danh sách, trạng thái, sắp đến hạn review).
8. **Báo cáo calibration** (viết hàm sẵn, **chưa bật** — cần ≥6 tháng dữ liệu): mỗi quý, theo từng người quyết: *"Anh dự báo ở mức tự tin 80%, thực tế đúng 55% — dự báo lift khuyến mãi của anh đang lạc quan ~20 điểm %"*.

**Rủi ro & xử lý**
- *Sếp thấy phiền vì bị hỏi kỳ vọng* → chỉ bắt buộc với quyết định lớn (big bet); quyết định nhỏ chỉ ghi tiêu đề + ngày.
- *Không đo được* → `review_sql` để trống thì đến hạn Jenny **hỏi người quyết** thay vì tự đo.

**Nghiệm thu:** 1 quyết định trong cuộc họp thật được ghi kèm kỳ vọng đo được; đến ngày review Jenny tự đăng đối chiếu.

---

### 11.2 — Decision brief + pre-mortem
*Ước lượng: 3–4 ngày (+2 nếu tạo Lark Doc thật)*

**Mục tiêu:** trước quyết định lớn, BOD có một tài liệu **chuẩn hoá, dựa trên số liệu thật**, thay vì mỗi người mang một góc nhìn và một bộ số riêng.

**Việc cần làm**
1. **Tool `decision_brief(topic, chat_id, decision_type)`** — gom dữ liệu từ BigQuery (`bq_recent_queries` + `bq_query`), tài liệu liên quan (`search_resources`), biên bản họp cũ, danh bạ tổ chức → soạn theo **template cố định** (kiểu 6-pager Amazon: bắt buộc viết thành văn, không bullet rỗng):
   - Bối cảnh (số liệu thật, không tính từ)
   - **Câu hỏi cần quyết là gì**
   - 2–4 phương án — mỗi phương án: mô tả · số liệu hậu thuẫn · rủi ro
   - **Khuyến nghị + lý do** (bắt buộc chọn 1)
   - Rủi ro chính & cách giảm
   - FAQ (câu hỏi BOD sẽ hỏi)
   - Ai cần quyết gì, trước ngày nào
2. **Đầu ra**: v1 lưu `.md` vào kho bộ nhớ (`memory_save` sẵn có) + gửi tóm tắt vào chat. v2 tạo **Lark Doc thật** — cần bổ sung `create_document()` trong `lark_user.py` (API docx tạo doc + append block; hiện chỉ mới đọc được doc). Tách thành việc riêng vì API block khá lằng nhằng.
3. **Tool `premortem(decision_id | topic)`**: sinh 8–12 lý do thất bại **dựa trên dữ liệu thật của LSR** theo khung *"12 tháng sau, việc này đã thất bại — vì sao?"* → gửi từng thành viên BOD xin bổ sung → gom vào hồ sơ quyết định.
   *Cơ sở: pre-mortem tăng ~30% khả năng nhận diện đúng nguyên nhân thất bại (Klein/HBR).*
4. **Red-team**: khi được hỏi, Jenny **phản biện chính khuyến nghị của mình** (steelman phía ngược lại) — thêm vào skill, không cần tool mới.

**Nghiệm thu:** yêu cầu brief cho 1 chủ đề thật (vd mở rộng kênh/đổi chính sách freeship) → nhận tài liệu đủ 7 phần, có khuyến nghị rõ ràng, số liệu truy được nguồn.

---

### 11.3 — Brief sáng dạng âm thanh ❌ **ĐÃ BỎ** *(2026-08-12, theo yêu cầu — code đã gỡ)*
*Giữ lại mô tả bên dưới để lưu vết lý do thiết kế, không triển khai.*

**Mục tiêu:** nghe brief trên đường đi làm thay vì đọc. Đây là **cách khả thi duy nhất để "tích hợp xe hơi"** (CarPlay/Android Auto đã khoá cửa với trợ lý bên thứ ba).

**Việc cần làm**
1. **Module mới `agent/jenny/tts.py`** — ElevenLabs multilingual (giọng tiếng Việt) hoặc Google Cloud TTS. Secret `ELEVENLABS_API_KEY` vào `agent.env`.
2. **Viết riêng bản để NGHE**: không TTS thẳng bản text có bullet/bảng. Thêm bước cho Claude viết `script_for_audio`: câu ngắn, số làm tròn, không markdown, có mở-thân-kết. Khoảng 2–3 phút đọc.
3. **Gửi**: xuất m4a/mp3 → `lark_user.send_file(chat_id, path)` (đường này đã chạy thật với Audio Overview của NotebookLM).
4. **Bật/tắt theo lịch**: thêm cột `audio bool` vào `scheduled_tasks` (migration `0010`) hoặc config `audio_brief` liệt kê id lịch cần đọc thành tiếng.
5. **V2 (sau)**: private podcast feed — endpoint `/podcast/<token>.xml` trên `jenny-web` + lưu file trên Supabase Storage → sếp subscribe 1 lần trong Apple Podcasts/Spotify, nghe qua CarPlay.

**Rủi ro & xử lý**
- *Chất lượng giọng tiếng Việt* → **test giọng trước** với 1 bản brief thật, cho BOD chọn; nếu không đạt thì dừng, không ép.
- *Chi phí* → ~0.10–0.30 USD/tập, không đáng kể; đặt giới hạn ký tự để tránh trôi.

**Nghiệm thu:** 6h30 sáng nhận file audio 2–3 phút, nghe rõ, số liệu đúng như bản chữ.

---

### 11.4 — Thẻ tương tác (Duyệt / Hỏi thêm / Giao việc)
*Ước lượng: 2–4 ngày · **có rủi ro kỹ thuật, cần spike trước***

**Mục tiêu:** sếp xử lý 1 chạm trên điện thoại thay vì gõ trả lời.

**Việc cần làm**
1. **SPIKE trước tiên (0.5 ngày)** — kiểm chứng: **tài khoản người dùng (user token) có gửi được thẻ tương tác `msg_type=interactive` kèm callback không?** Lark vốn thiết kế thẻ tương tác cho **bot/app**, không cho user account.
   - Nếu **được** → làm thẳng.
   - Nếu **không** → phương án B: **bot (tenant token) gửi thẻ** trong cùng chat (bot phải là thành viên chat), callback nhận qua `events.py` (`card.action.trigger`) hoặc HTTP callback về `jenny-web`.
   - Nếu cả hai không xong → phương án C: quy ước trả lời nhanh bằng số (1/2/3) hoặc thả emoji — vẫn giảm ma sát đáng kể.
2. Áp dụng cho 3 luồng: **duyệt kết quả việc giao**, **duyệt biên bản họp**, **xác nhận ghi quyết định vào sổ**.
3. Ghi nhận ai bấm nút vào bảng tương ứng (`assignments.status`, `meetings.status`, `decisions`).

**Nghiệm thu:** duyệt 1 biên bản họp bằng 1 chạm trên điện thoại, trạng thái đổi đúng trong DB.

---

### 11.5 — Danh sách ngoại lệ S&OP + ngưỡng cảnh báo kế hoạch (signpost)
*Ước lượng: 3 ngày · phụ thuộc 10.2 (dùng lại hạ tầng monitor)*

**Mục tiêu:** họp S&OP chỉ bàn **ngoại lệ**, không đọc lại toàn bộ số liệu; và mỗi kế hoạch lớn có **ngưỡng kích hoạt review** tự động.

**Việc cần làm**
1. **Nhận file S&OP đã chốt** (đã đưa vào Brief sáng: nhắc anh Kim Tuấn Anh gửi) → lưu vào kho tài nguyên/bộ nhớ để đối chiếu tự động.
2. **Lịch "Chuẩn bị họp S&OP"** chạy trước kỳ họp (config `sop_cycle`: ngày trong tháng): sinh **danh sách ngoại lệ**
   - SKU/nhóm lệch kế hoạch quá ngưỡng (cả trên lẫn dưới)
   - Tồn kho: số tuần phủ bất thường, hàng già, nguy cơ đứt hàng top-seller
   - Sell-through vs kế hoạch mua (open-to-buy)
   - **SKU ổn định thì loại khỏi agenda** — đây mới là phần tiết kiệm thời gian họp
   - Kết bằng: 3–5 việc cần BOD quyết trong kỳ họp
3. **Signpost watchlist**: dùng lại bảng `monitors` với `kind='signpost'` + `linked_decision_id` — mỗi kế hoạch lớn gắn điều kiện kích hoạt (vd *"GMV rolling 3 tháng brand B < X% → review kế hoạch mở rộng"*). Khi chạm ngưỡng → cảnh báo *"điều kiện review kế hoạch X đã chạm"* kèm liên kết tới quyết định gốc trong sổ.
4. **MER blended theo tuần** (doanh thu / tổng chi quảng cáo) theo brand + cảnh báo khi ROAS kênh tự khai lệch xa MER blended (dấu hiệu tự huyễn hoặc kinh điển của e-commerce).

**Nghiệm thu:** trước kỳ S&OP nhận danh sách ngoại lệ (không phải báo cáo đầy đủ); 1 signpost chạm ngưỡng → cảnh báo có dẫn chiếu quyết định gốc.

---

## 12. Phụ thuộc, thứ tự làm & tổng hợp thay đổi hệ thống

### Thứ tự đề xuất
```
10.1 voice note ──────────────┐ (độc lập, làm trước để dùng hằng ngày)
10.2 anomaly ─┬── 10.3 digest │
              └── 11.5 signpost/S&OP
10.4 auto-chase ── 10.3 (chung chỉ số hoàn thành cam kết)
11.1 decisions ─┬── 11.2 brief + pre-mortem
                └── (calibration — bật sau ≥6 tháng)
11.3 audio brief (độc lập)
11.4 thẻ tương tác (spike trước — có thể trượt sang sau)
```

### Migration mới
| File | Nội dung |
|---|---|
| `0008_monitors.sql` | `monitors` (anomaly + signpost) · `anomaly_events` |
| `0009_decisions.sql` | `decisions` (sổ quyết định + kết quả thực tế) |
| `0010_schedule_audio.sql` | thêm cột `audio` vào `scheduled_tasks` *(nếu chọn phương án cột thay vì config)* |

### Module mới
`anomaly.py` · `decisions.py` · `tts.py`

### Tool mới cần đăng ký trong `agent.py`
`anomaly_recent` · `assignment_stats` · `decision_log` · `decision_list` · `decision_update` · `decision_brief` · `premortem` *(+ `monitor_*` nếu quản lý monitor bằng chat thay vì dashboard)*

### Config mới
`voice_note` · `anomaly_defaults` (cooldown, giờ im lặng, ngày blackout) · `assignment_chase` · `tts` · `sop_cycle` · `audio_brief`

### Secret mới
`ELEVENLABS_API_KEY` (hoặc credentials Google TTS) trong `config/secrets/agent.env`

### Trang dashboard mới
`/monitors` · `/decisions`

---

## 13. Quy ước tài liệu & quy trình *(bắt buộc)*

> **Khi có thay đổi về kế hoạch hoặc tính năng lớn: cập nhật `PLAN.md` và `FEATURES.md` TRƯỚC, rồi mới commit git.**

- `PLAN.md` — kế hoạch, lộ trình, trạng thái từng phase, kế hoạch chi tiết các đợt sắp làm.
- `FEATURES.md` — mô tả tính năng **đang có thật** (đã deploy), kèm phần roadmap tóm tắt.
- `RESEARCH.md` — nghiên cứu nền, cơ sở lý do cho các lựa chọn tính năng.
- Sửa xong tài liệu → commit **cùng một commit** với code của tính năng đó (không tách rời, tránh tài liệu trôi khỏi thực tế).
- Tính năng nhỏ / sửa lỗi: không bắt buộc sửa 2 file này.
