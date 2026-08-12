-- Sổ quyết định: ghi quyết định kèm KỲ VỌNG ĐO ĐƯỢC, đến hạn tự đo kết quả thật.
-- Cơ sở: decision journal + calibration (Tetlock) — con người không duy trì nổi sổ này,
-- AI thì có. Chạy trong Supabase SQL Editor, sau đó: NOTIFY pgrst, 'reload schema';

create table if not exists decisions (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  decided_at timestamptz default now(),
  decider_open_id text,
  decider_name text,
  -- big_bet: hiếm, hệ trọng (cần brief + pre-mortem) | cross_cutting: định kỳ, liên phòng ban
  -- (vd S&OP) | delegated: thường xuyên, ít rủi ro
  type text default 'delegated',
  reversible boolean,                    -- cửa 2 chiều (Bezos Type 2) → quyết nhanh
  context_md text default '',
  options_md text default '',
  chosen_option text default '',
  expected_outcome text default '',      -- mô tả kỳ vọng bằng lời
  expected_metric jsonb,                 -- {metric, target, unit, direction}
  confidence int,                        -- % tự tin của người quyết (cho calibration)
  review_at timestamptz,                 -- đến mốc này Jenny tự đo lại
  review_sql text,                       -- SQL trả 1 dòng 1 cột v — bỏ trống thì Jenny đi hỏi
  actual_outcome text default '',
  actual_value numeric,
  outcome_verdict text,                  -- dat | khong_dat | mot_phan
  status text default 'open',            -- open | reviewed | cancelled
  source_kind text default 'chat',       -- meeting | chat | assignment
  source_id text default '',
  channel text default 'lark',
  chat_id text default '',
  created_at timestamptz default now()
);

create index if not exists decisions_review_idx on decisions (review_at)
  where status = 'open';

alter table decisions enable row level security;
