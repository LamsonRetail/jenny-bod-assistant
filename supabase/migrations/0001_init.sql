-- Jenny — LSR BOD Assistant · Schema khởi tạo
-- Chạy trong Supabase SQL Editor hoặc `supabase db push`

create extension if not exists pgcrypto;

-- ============ Hội thoại & tin nhắn ============

create table conversations (
  id          uuid primary key default gen_random_uuid(),
  channel     text not null check (channel in ('telegram','lark','zalo','messenger')),
  chat_id     text not null,            -- id chat/group phía kênh
  title       text,
  is_group    boolean not null default false,
  whitelisted boolean not null default false,  -- chỉ chat được duyệt mới được trả lời
  created_at  timestamptz not null default now(),
  unique (channel, chat_id)
);

create table messages (
  id              bigint generated always as identity primary key,
  conversation_id uuid not null references conversations(id) on delete cascade,
  session_id      text,                 -- id phiên agent xử lý tin này
  direction       text not null check (direction in ('in','out')),
  sender_id       text,
  sender_name     text,
  content         text not null,
  tokens_input    integer,
  tokens_output   integer,
  created_at      timestamptz not null default now()
);
create index messages_conversation_idx on messages (conversation_id, created_at desc);

-- ============ Vận hành agent (nguồn cho dashboard) ============

create table tool_calls (
  id              bigint generated always as identity primary key,
  session_id      text,
  conversation_id uuid references conversations(id) on delete set null,
  tool_name       text not null,
  args            jsonb,
  result_summary  text,                 -- tóm tắt kết quả, không lưu payload lớn
  status          text not null default 'ok' check (status in ('ok','error')),
  error           text,
  duration_ms     integer,
  created_at      timestamptz not null default now()
);
create index tool_calls_created_idx on tool_calls (created_at desc);

create table token_usage (
  id                 bigint generated always as identity primary key,
  day                date not null,
  session_id         text,
  model              text,
  input_tokens       bigint not null default 0,
  output_tokens      bigint not null default 0,
  cache_read_tokens  bigint not null default 0,
  cache_write_tokens bigint not null default 0,
  created_at         timestamptz not null default now()
);
create index token_usage_day_idx on token_usage (day desc);

-- ============ Skills & configs (quản lý từ dashboard) ============

create table skills (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,     -- slug: vd 'bigquery-analytics'
  description text not null,            -- 1 dòng — agent dùng để chọn skill
  content_md  text not null,            -- nội dung skill dạng markdown (general)
  enabled     boolean not null default true,
  version     integer not null default 1,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table configs (
  key         text primary key,         -- vd 'bq_data_dictionary', 'drive_memory_folder'
  value       jsonb not null,
  description text,
  updated_at  timestamptz not null default now()
);

-- ============ Lịch chạy tự động ============

create table scheduled_tasks (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  cron        text not null,            -- cron expression, giờ VN (Asia/Ho_Chi_Minh)
  prompt      text not null,            -- yêu cầu giao cho agent mỗi lần chạy
  channel     text,                     -- kênh gửi kết quả
  chat_id     text,                     -- group/chat nhận kết quả
  enabled     boolean not null default true,
  last_run_at timestamptz,
  created_at  timestamptz not null default now()
);

-- ============ Multi-agent (A2A) — chuẩn bị sẵn ============

create table agents (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique,     -- 'jenny', sau này: 'finance-agent', ...
  description text,
  status      text not null default 'active' check (status in ('active','inactive')),
  created_at  timestamptz not null default now()
);

create table agent_messages (
  id           bigint generated always as identity primary key,
  from_agent   text not null references agents(name),
  to_agent     text not null references agents(name),
  content      jsonb not null,
  status       text not null default 'pending' check (status in ('pending','processing','done','error')),
  created_at   timestamptz not null default now(),
  processed_at timestamptz
);
create index agent_messages_inbox_idx on agent_messages (to_agent, status, created_at);

-- ============ RLS: khóa mặc định ============
-- Service role key (chỉ dùng trên VPS/dashboard server-side) bỏ qua RLS.
-- Không tạo policy nào → anon key không đọc/ghi được gì.

alter table conversations   enable row level security;
alter table messages        enable row level security;
alter table tool_calls      enable row level security;
alter table token_usage     enable row level security;
alter table skills          enable row level security;
alter table configs         enable row level security;
alter table scheduled_tasks enable row level security;
alter table agents          enable row level security;
alter table agent_messages  enable row level security;

-- ============ Seed dữ liệu ban đầu ============

insert into agents (name, description) values
  ('jenny', 'LSR BOD Assistant — agent chính');

insert into configs (key, value, description) values
  ('drive_memory_folder',
   '{"folder_id": "1yNdY2zCc0BOiIxY4p2eQ9w_YAbIeym4y", "name": "Jenny-BOD-Memory", "index_file_id": "1M8AwtaA-LlHpg_VzlG2jJIpsDUOqEHDg"}',
   'Thư mục Google Drive lưu bộ nhớ .md của Jenny'),
  ('reply_rules',
   '{"only_when_tagged_in_group": true, "language": "vi"}',
   'Quy tắc trả lời trong group'),
  ('bq_data_dictionary',
   '{"status": "chua_cau_hinh", "note": "Điền project_id, dataset và link/nội dung data dictionary"}',
   'Định nghĩa các bảng BigQuery cho skill query dữ liệu');
