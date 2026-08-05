# SETUP — Phase 0

> **Trạng thái 2026-08-04**: ✅ A (Supabase: project tạo xong, schema đã chạy,
> keys trong `config/secrets/supabase.env`, kết nối đã kiểm tra) ·
> ✅ B (VPS) · ✅ C (Claude Max login OK, đã test) ·
> ⏳ D (Google service account) · ⏳ E (Telegram bot)

## A. Tạo Supabase project mới (làm 1 lần, ~5 phút)

1. Vào https://supabase.com/dashboard → **New project**
2. Chọn organization → đặt tên: `jenny-bod` → Database password: bấm
   **Generate** và lưu lại (dán vào `config/secrets/supabase.env` bên dưới)
3. Region: **Southeast Asia (Singapore)** → **Create new project**, chờ ~2 phút
4. Lấy thông tin kết nối tại **Project Settings → API**:
   - `Project URL`  → `SUPABASE_URL`
   - `anon public`  → `SUPABASE_ANON_KEY` (cho dashboard client-side, bị RLS chặn)
   - `service_role` → `SUPABASE_SERVICE_KEY` (**bí mật** — chỉ để trên VPS)
5. Điền 3 giá trị trên vào `config/secrets/supabase.env` (copy từ
   `config/supabase.env.example`)
6. Chạy schema: **SQL Editor → New query** → dán toàn bộ
   `supabase/migrations/0001_init.sql` → **Run**. Kết quả mong đợi:
   "Success. No rows returned" và 9 bảng xuất hiện trong Table Editor.

## B. VPS Hostinger (tự động hóa bằng script, xem trạng thái bên dưới)

Thông tin truy cập: `config/secrets/hostinger.env`

- [x] SSH key riêng cho VPS (`~/.ssh/jenny_vps`) + cài lên server *(2026-08-04)*
- [x] Đổi mật khẩu root — mật khẩu mới trong `hostinger.env` *(2026-08-04)*
- [x] Cài đặt: python3-venv, git, Claude Code CLI 2.1.221 *(2026-08-04)*
- [x] Tạo thư mục `/opt/jenny` *(2026-08-04)*

Server: Ubuntu 24.04 LTS, 2 vCPU, 8GB RAM, 96GB disk (hostname `hapas-ai`).

## C. Đăng nhập Claude Max trên VPS (cần anh/chị làm — 2 phút)

Claude Code đăng nhập bằng OAuth nên cần trình duyệt của anh/chị:

```bash
ssh -i ~/.ssh/jenny_vps root@187.77.135.158 -t "claude /login"
```

→ chọn **Claude account with subscription** → mở URL hiện ra bằng trình duyệt
trên máy Mac → đăng nhập tài khoản Max → copy code dán ngược lại terminal.
Xong thì báo Jenny (Claude Code) để kiểm tra lại bằng lệnh test.

## D. Google service account cho Drive (Phase 0.5 — trước Phase 1)

1. https://console.cloud.google.com → tạo project `jenny-bod` (hoặc dùng chung
   project BigQuery của LSR nếu muốn 1 service account cho cả hai)
2. **IAM & Admin → Service Accounts → Create**: tên `jenny-agent`
3. Tạo key JSON → tải về → lưu thành `config/secrets/google-service-account.json`
4. Bật **Google Drive API** (APIs & Services → Enable APIs)
5. Trên Drive: share thư mục `Jenny-BOD-Memory` cho email của service account
   (dạng `jenny-agent@<project>.iam.gserviceaccount.com`) quyền **Editor**
6. (Cho BigQuery sau này: cấp thêm role `BigQuery Data Viewer` + `BigQuery Job User`)

## E. Telegram bot (trước Phase 1 — 2 phút)

1. Trong Telegram, chat với **@BotFather** → `/newbot`
2. Tên hiển thị: `Jenny — LSR BOD Assistant`, username dạng `lsr_jenny_bot`
3. Copy token → điền `TELEGRAM_BOT_TOKEN` vào `config/secrets/agent.env`
4. Trong BotFather: `/setprivacy` → chọn bot → **Disable** (để bot đọc được
   tin nhắn group khi được tag)
