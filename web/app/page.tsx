import { sb, fmtNum } from "@/lib/db";

export const dynamic = "force-dynamic";

function daysAgoISO(n: number) {
  const d = new Date(Date.now() - n * 86400_000);
  return d.toISOString().slice(0, 10);
}

export default async function Overview() {
  const db = sb();
  const since14 = daysAgoISO(13);
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Ho_Chi_Minh" });

  const [{ data: usage }, { count: msgToday }, { count: toolToday }, { count: convs }] =
    await Promise.all([
      db.from("token_usage").select("day,input_tokens,output_tokens,cache_read_tokens")
        .gte("day", since14),
      db.from("messages").select("*", { count: "exact", head: true })
        .gte("created_at", `${today}T00:00:00+07:00`),
      db.from("tool_calls").select("*", { count: "exact", head: true })
        .gte("created_at", `${today}T00:00:00+07:00`),
      db.from("conversations").select("*", { count: "exact", head: true }),
    ]);

  const byDay = new Map<string, { inp: number; out: number; cache: number }>();
  for (let i = 13; i >= 0; i--) byDay.set(daysAgoISO(i), { inp: 0, out: 0, cache: 0 });
  for (const r of usage ?? []) {
    const d = byDay.get(r.day) ?? { inp: 0, out: 0, cache: 0 };
    d.inp += r.input_tokens; d.out += r.output_tokens; d.cache += r.cache_read_tokens;
    byDay.set(r.day, d);
  }
  const days = [...byDay.entries()];
  const max = Math.max(1, ...days.map(([, v]) => v.out + v.cache));
  const totOut = days.reduce((s, [, v]) => s + v.out, 0);
  const totCache = days.reduce((s, [, v]) => s + v.cache, 0);

  return (
    <>
      <h2>Tổng quan</h2>
      <div className="cards">
        <div className="card"><div className="label">Tin nhắn hôm nay</div><div className="value">{fmtNum(msgToday ?? 0)}</div></div>
        <div className="card"><div className="label">Tool calls hôm nay</div><div className="value">{fmtNum(toolToday ?? 0)}</div></div>
        <div className="card"><div className="label">Hội thoại</div><div className="value">{fmtNum(convs ?? 0)}</div></div>
        <div className="card"><div className="label">Output tokens (14 ngày)</div><div className="value">{fmtNum(totOut)}</div></div>
        <div className="card"><div className="label">Cache read (14 ngày)</div><div className="value">{fmtNum(totCache)}</div></div>
      </div>

      <h3>Token 14 ngày (đậm: output · nhạt: cache read)</h3>
      <div className="chart">
        {days.map(([day, v]) => (
          <div className="col" key={day} title={`${day}: out ${fmtNum(v.out)}, cache ${fmtNum(v.cache)}`}>
            <div className="bar cache" style={{ height: `${(v.cache / max) * 100}%` }} />
            <div className="bar" style={{ height: `${Math.max((v.out / max) * 100, 1)}%` }} />
            <div className="day">{day.slice(8)}</div>
          </div>
        ))}
      </div>
    </>
  );
}
