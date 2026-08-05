import { createClient } from "@supabase/supabase-js";

export function sb() {
  return createClient(
    process.env.SUPABASE_URL!,
    process.env.SUPABASE_SERVICE_KEY!,
    { auth: { persistSession: false } }
  );
}

export function fmtTime(ts: string) {
  return new Date(ts).toLocaleString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit",
  });
}

export function fmtNum(n: number) {
  return new Intl.NumberFormat("vi-VN").format(n);
}
