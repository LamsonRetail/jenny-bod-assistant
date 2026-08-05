# Jenny — LSR BOD Assistant · Kế hoạch tổng thể

> Trạng thái: **CHỜ DUYỆT** — chưa bắt đầu prototype cho đến khi được confirm.
> Cập nhật: 2026-08-04

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

### Phase 0 — Nền móng *(≈ nửa ngày)*
- Tạo Supabase project mới (hướng dẫn từng bước) + chạy migration schema
- Setup VPS: SSH key, đổi mật khẩu root, Python, Claude Code CLI + đăng nhập Max
- Repo cấu trúc: `agent/` (VPS) + `web/` (Vercel) + `supabase/migrations/`

### Phase 1 — Prototype *(cần confirm kế hoạch này trước)* *(≈ 1–2 ngày)*
- Telegram bot (long polling): add vào group, tag `@jenny` thì trả lời
- Agent core với Claude Agent SDK, nạp skill từ Supabase
- 4 skill đầu: **hỏi đáp + web search**, **query BigQuery** (data dictionary
  trong `configs`), **kiến thức nội bộ** (đọc tài liệu được khai báo),
  **memory** (ghi/tra cứu .md trên Drive theo mục 6)
- Log đầy đủ messages / tool_calls / token_usage vào Supabase
- **→ Demo cho anh/chị duyệt trước khi làm tiếp**

### Phase 2 — Dashboard Vercel *(≈ 1–2 ngày)*
- Trang theo dõi: tool calls, token usage theo ngày, hội thoại gần đây
- Quản lý skills (thêm/xóa/sửa, bật/tắt) và configs
- Quản lý scheduled tasks

### Phase 3 — Lark + báo cáo định kỳ *(≈ 2–3 ngày)*
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

### Phase 4 — Zalo *(≈ 1–2 ngày, cần domain + Zalo OA)*
- Webhook HTTPS qua Caddy, tích hợp Zalo OA

### Phase 5 — Multi-agent + Messenger *(sau)*
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
