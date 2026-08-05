-- Theo dõi meeting notes: họp có Jenny tham gia → notes → duyệt → phát hành
-- Chạy trong Supabase SQL Editor

create table meetings (
  id              uuid primary key default gen_random_uuid(),
  event_id        text not null unique,
  title           text,
  start_at        timestamptz,
  end_at          timestamptz,
  creator_open_id text,
  creator_name    text,
  attendees       jsonb not null default '[]',  -- [{open_id, name}]
  status          text not null default 'awaiting_content'
                  check (status in ('awaiting_content','draft','distributed','skipped')),
  notes_md        text,
  file_token      text,                          -- file notes trên Lark Drive
  tasks_created   integer not null default 0,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index meetings_status_idx on meetings (status, end_at desc);

alter table meetings enable row level security;
