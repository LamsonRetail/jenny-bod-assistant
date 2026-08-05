-- Danh bạ nhân sự: đồng bộ từ Lark Contacts + ghi chú Jenny tự học
-- Chạy trong Supabase SQL Editor

create table people (
  open_id         text primary key,
  name            text not null,
  job_title       text,
  department      text,          -- phòng ban trực tiếp
  department_path text,          -- đường dẫn đầy đủ: Cty > Khối > Phòng
  is_leader       boolean not null default false,
  learned_notes   text not null default '',  -- Jenny tự học (chỉ thông tin công việc)
  updated_at      timestamptz not null default now()
);

alter table people enable row level security;
