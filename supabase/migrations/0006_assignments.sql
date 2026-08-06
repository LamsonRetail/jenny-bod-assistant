-- Việc BOD giao cho nhân sự — Jenny tạo, theo dõi, đánh giá, báo cáo lại BOD
-- Chạy trong Supabase SQL Editor

create table assignments (
  id               uuid primary key default gen_random_uuid(),
  title            text not null,
  context          text,                 -- bối cảnh vì sao cần làm
  expected_outcome text,                 -- đầu ra mong đợi (tiêu chí đánh giá)
  deadline         timestamptz,
  assigner_open_id text,                 -- thành viên BOD giao việc
  assigner_name    text,
  pic_open_id      text not null,        -- người chịu trách nhiệm (PIC)
  pic_name         text,
  lark_task_guid   text,                 -- task tương ứng trên Lark Task
  status           text not null default 'assigned'
                   check (status in ('assigned','in_review','needs_more','done','cancelled')),
  result_summary   text,                 -- tổng hợp cuối để BOD ra quyết định
  last_reminded_at timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index assignments_status_idx on assignments (status, deadline);
create index assignments_pic_idx on assignments (pic_open_id, status);

alter table assignments enable row level security;
