-- Danh bạ tài nguyên: link/file/doc được share trong chat — Jenny tự index
-- Chạy trong Supabase SQL Editor

create table resources (
  id           bigint generated always as identity primary key,
  channel      text not null default 'lark',
  chat_id      text,
  chat_name    text,
  sender_id    text,
  kind         text not null check (kind in ('link','lark_doc','file')),
  url          text,                 -- link gốc (link ngoài hoặc link doc Lark)
  file_token   text,                 -- token file Lark (nếu là file đính kèm)
  title        text,
  excerpt      text,                 -- trích đoạn đầu để tra cứu nhanh
  context_note text,                 -- câu chat đi kèm khi share
  created_at   timestamptz not null default now(),
  unique (url, chat_id)
);
create index resources_created_idx on resources (created_at desc);

alter table resources enable row level security;
