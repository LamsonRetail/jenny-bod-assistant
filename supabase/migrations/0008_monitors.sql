-- Giám sát số liệu: cảnh báo bất thường (anomaly) + ngưỡng kích hoạt review kế hoạch (signpost)
-- Chạy trong Supabase SQL Editor, sau đó: NOTIFY pgrst, 'reload schema';

-- Định nghĩa các phép giám sát.
-- HỢP ĐỒNG SQL:
--  · kind='anomaly'  → sql phải trả về 2 cột: d (DATE), v (NUMERIC) — 1 dòng/ngày,
--    phủ ít nhất 5 lần xuất hiện của cùng thứ trong tuần (khuyến nghị 8 tuần gần nhất).
--  · kind='signpost' → sql trả về 1 dòng, 1 cột v (NUMERIC); so với threshold_value.
create table if not exists monitors (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  kind text not null default 'anomaly',          -- anomaly | signpost
  sql text not null,
  metric_label text default '',                  -- tên hiển thị của chỉ số
  unit text default '',                          -- VND | % | đơn ...
  baseline_method text default 'weekday_median', -- weekday_median | rolling_median
  threshold_high numeric default 3.0,            -- |z| >= → mức cao
  threshold_med numeric default 2.0,             -- |z| >= → mức trung bình
  direction text default 'both',                 -- both | down | up (chỉ báo khi giảm/tăng)
  threshold_value numeric,                       -- dùng cho signpost
  check_cron text default '0 9,15,21 * * *',     -- giờ VN
  cooldown_hours int default 12,
  channel text default 'lark',
  chat_id text not null,
  enabled boolean default true,
  linked_decision_id uuid,                       -- signpost gắn với quyết định nào (Đợt 2)
  note text default '',
  last_checked_at timestamptz,
  last_alert_at timestamptz,
  created_at timestamptz default now()
);

-- Lịch sử phát hiện bất thường (nguồn cho digest thứ 2 và dashboard)
create table if not exists anomaly_events (
  id uuid primary key default gen_random_uuid(),
  monitor_id uuid references monitors(id) on delete cascade,
  monitor_name text,
  observed_at timestamptz default now(),
  value numeric,
  baseline numeric,
  z_score numeric,
  pct_change numeric,
  severity text,                                 -- high | medium | low
  narrative text default '',
  notified boolean default false,
  chat_id text,
  acknowledged_by text,
  created_at timestamptz default now()
);

create index if not exists anomaly_events_created_idx on anomaly_events (created_at desc);

alter table monitors enable row level security;
alter table anomaly_events enable row level security;
