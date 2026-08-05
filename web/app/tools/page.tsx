import { sb, fmtTime } from "@/lib/db";

export const dynamic = "force-dynamic";

export default async function Tools() {
  const { data } = await sb()
    .from("tool_calls")
    .select("id,tool_name,args,status,error,result_summary,created_at")
    .order("created_at", { ascending: false })
    .limit(80);

  return (
    <>
      <h2>Tool calls gần đây</h2>
      <table>
        <thead>
          <tr><th>Lúc</th><th>Tool</th><th>Tham số</th><th>Trạng thái</th></tr>
        </thead>
        <tbody>
          {(data ?? []).map((t) => (
            <tr key={t.id}>
              <td>{fmtTime(t.created_at)}</td>
              <td className="mono">{t.tool_name}</td>
              <td className="mono">{t.args ? JSON.stringify(t.args).slice(0, 160) : ""}</td>
              <td>
                <span className={`badge ${t.status === "ok" ? "ok" : "error"}`}>{t.status}</span>
                {t.error ? <div className="mono">{t.error.slice(0, 120)}</div> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
