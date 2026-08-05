# Jenny — LSR BOD Assistant

Trợ lý AI cho Ban điều hành LSR: trả lời trong group chat (Telegram/Lark/Zalo),
query BigQuery, quản lý task & lịch qua Lark, báo cáo định kỳ, bộ nhớ .md trên
Google Drive.

- Kế hoạch tổng thể: [PLAN.md](PLAN.md)
- Hướng dẫn setup: [SETUP.md](SETUP.md)

## Cấu trúc

```
agent/                  # code agent chạy trên VPS Hostinger (Claude Agent SDK)
web/                    # dashboard Next.js deploy Vercel
supabase/migrations/    # schema database
config/                 # file .example (an toàn commit)
config/secrets/         # secrets thật — GITIGNORED, không commit
```

## Hạ tầng

| Thành phần | Ở đâu |
|---|---|
| Agent core | VPS Hostinger `187.77.135.158`, thư mục `/opt/jenny` |
| LLM | Claude Max (Claude Code CLI + Agent SDK) |
| Database | Supabase project `jenny-bod` |
| Bộ nhớ nội dung | Google Drive `Jenny-BOD-Memory` |
| Dashboard | Vercel |
