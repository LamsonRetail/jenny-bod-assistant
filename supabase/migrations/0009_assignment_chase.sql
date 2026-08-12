-- Đôn đốc việc BOD giao: đếm số lần đã nhắc + mốc escalate lên người giao
-- Chạy trong Supabase SQL Editor, sau đó: NOTIFY pgrst, 'reload schema';

alter table assignments add column if not exists reminder_count int default 0;
alter table assignments add column if not exists escalated_at timestamptz;
alter table assignments add column if not exists completed_at timestamptz;
