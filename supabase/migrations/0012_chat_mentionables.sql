-- Sổ đăng ký "ai tag được" trong từng chat — gồm cả BOT APP.
--
-- Lý do cần bảng này: API thành viên của Lark (`im/v1/chats/:id/members`) chỉ nhận
-- member_id_type = user_id/union_id/open_id và KHÔNG trả về bot app; endpoint
-- `/members/bot` thì 404. Nhưng bot app VẪN có open_id dạng `ou_…` và VẪN tag được —
-- id đó xuất hiện trong mảng `mentions` của những tin đã tag chúng.
-- => Jenny học dần danh sách này từ lịch sử tin nhắn rồi dùng để tag theo tên.

create table if not exists chat_mentionables (
  chat_id text not null,
  open_id text not null,
  name text default '',
  is_bot boolean default false,          -- true nếu không có trong danh bạ `people`
  last_seen_at timestamptz default now(),
  created_at timestamptz default now(),
  primary key (chat_id, open_id)
);

create index if not exists chat_mentionables_bot_idx
  on chat_mentionables (chat_id) where is_bot;

alter table chat_mentionables enable row level security;
