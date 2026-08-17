-- 0012 — Mở Jenny cho toàn công ty + chặn chủ đề nhạy cảm + trả lời nhất quán
--
-- 1. open_access: mọi chat đều dùng được ngay (không cần admin /approve)
-- 2. restricted_topics: câu hỏi NHÂN SỰ / TÀI CHÍNH bị từ chối TRƯỚC khi gọi LLM
--    (chặn ở code, không phụ thuộc prompt) — admin vẫn được trả lời đầy đủ
-- 3. assignment_admins: chỉ 3 người được GIAO VIỆC qua Jenny
-- 4. answer_cache: câu hỏi giống nhau → trả về ĐÚNG câu trả lời cũ (nhất quán)

create extension if not exists pg_trgm;

create table if not exists answer_cache (
  id           uuid primary key default gen_random_uuid(),
  norm_key     text not null,              -- câu hỏi đã chuẩn hoá (bỏ dấu, bỏ nhiễu)
  tier         text not null default 'user', -- user | admin (khác quyền → khác cache)
  question     text not null,              -- câu gốc lần đầu (để đối chiếu)
  answer       text not null,
  hits         int  not null default 0,
  created_at   timestamptz not null default now(),
  last_hit_at  timestamptz
);
create unique index if not exists answer_cache_key on answer_cache (norm_key, tier);
create index if not exists answer_cache_trgm on answer_cache using gin (norm_key gin_trgm_ops);
create index if not exists answer_cache_created on answer_cache (created_at desc);

-- Tìm câu hỏi TƯƠNG TỰ trong hạn TTL. Trả câu trả lời giống hệt để đảm bảo nhất quán.
create or replace function find_cached_answer(
  p_norm text, p_tier text, p_ttl_hours int, p_min_sim double precision default 0.86
) returns table (id uuid, answer text, question text, sim double precision) as $$
  select c.id, c.answer, c.question, similarity(c.norm_key, p_norm)::double precision as sim
  from answer_cache c
  where c.tier = p_tier
    and c.created_at > now() - make_interval(hours => p_ttl_hours)
    and (c.norm_key = p_norm or similarity(c.norm_key, p_norm) >= p_min_sim)
  order by (c.norm_key = p_norm) desc, sim desc
  limit 1;
$$ language sql stable;

-- ---- Config (sửa được trên Supabase, không cần deploy lại) ----
insert into configs (key, value) values
  ('open_access', '{"enabled": true, "note": "true = mọi chat dùng được ngay; false = phải admin /approve"}'::jsonb)
on conflict (key) do update set value = excluded.value;

insert into configs (key, value) values
  ('restricted_topics', '{
    "enabled": true,
    "admin_bypass": true,
    "nhân sự": ["lương", "bang luong", "bảng lương", "thu nhập", "thưởng", "hợp đồng lao động",
                "bhxh", "bảo hiểm xã hội", "sa thải", "nghỉ việc", "thôi việc", "tuyển dụng",
                "ứng viên", "đánh giá nhân viên", "kỷ luật", "thai sản", "nghỉ phép", "chấm công",
                "tăng lương", "nhân sự", "payroll", "salary", "headcount", "hr"],
    "allow_phrases": ["san luong", "so luong", "chat luong", "khoi luong", "trong luong",
                      "luu luong", "dinh luong", "luong ton", "luong hang", "luong don",
                      "lo hang", "lo trinh", "lo san xuat", "lo dat", "lo moi", "so lo"],
    "tài chính": ["lợi nhuận", "chi phí", "giá vốn", "cogs", "dòng tiền", "cash flow", "công nợ",
                  "thuế", "vat", "báo cáo tài chính", "p&l", "pnl", "ebitda", "ngân sách",
                  "budget", "định giá", "cổ phần", "kế toán", "hóa đơn", "lỗ", "margin",
                  "biên lợi nhuận", "vốn", "đầu tư", "profit", "cost"]
  }'::jsonb)
on conflict (key) do update set value = excluded.value;

-- Chỉ 3 người được yêu cầu GIAO VIỆC (assignment_*). open_id lấy từ danh bạ Lark đã sync.
insert into configs (key, value) values
  ('assignment_admins', '{
    "lark": ["ou_997ec058ee5939cba91b86899b4e31b2",
             "ou_ee51cc6148fdb3a0e3427a64cc5a367e",
             "ou_1b54e216c36677214bd942c5a4800a79"],
    "telegram": [],
    "names": ["Nguyễn Trần Thi - BOD", "LÊ MẠNH CHUNG - CEO", "Nguyễn Tuấn Việt Sơn (B) - GDKD"],
    "note": "Telegram: người dùng gửi /id cho bot rồi thêm số vào mảng telegram."
  }'::jsonb)
on conflict (key) do update set value = excluded.value;

insert into configs (key, value) values
  ('answer_cache', '{"enabled": true, "ttl_hours": 12, "min_similarity": 0.86}'::jsonb)
on conflict (key) do update set value = excluded.value;
