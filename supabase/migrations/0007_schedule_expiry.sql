-- Lịch chạy có hạn dừng: quá expires_at thì scheduler tự tắt (enabled=false)
-- Chạy trong Supabase SQL Editor

alter table scheduled_tasks add column if not exists expires_at timestamptz;
