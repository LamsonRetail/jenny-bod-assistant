import { sb, fmtNum } from "@/lib/db";

export const dynamic = "force-dynamic";

const STATUS_LABEL: Record<string, { text: string; cls: string }> = {
  awaiting_content: { text: "chờ nội dung", cls: "off" },
  draft: { text: "chờ duyệt", cls: "off" },
  distributed: { text: "đã phát hành", cls: "ok" },
  skipped: { text: "bỏ qua", cls: "off" },
};

export default async function Meetings() {
  const { data } = await sb()
    .from("meetings").select("*")
    .order("end_at", { ascending: false }).limit(100);

  const rows = data ?? [];
  const active = rows.filter((m) => m.status !== "skipped");
  const distributed = rows.filter((m) => m.status === "distributed");
  const pending = rows.filter((m) => ["awaiting_content", "draft"].includes(m.status));
  const totalTasks = rows.reduce((s, m) => s + (m.tasks_created ?? 0), 0);

  return (
    <>
      <h2>Theo dõi họp</h2>
      <div className="cards">
        <div className="card"><div className="label">Tổng cuộc họp</div><div className="value">{fmtNum(active.length)}</div></div>
        <div className="card"><div className="label">Notes đã duyệt & phát hành</div><div className="value">{fmtNum(distributed.length)}</div></div>
        <div className="card"><div className="label">Đang chờ xử lý</div><div className="value">{fmtNum(pending.length)}</div></div>
        <div className="card"><div className="label">Task đã tạo từ họp</div><div className="value">{fmtNum(totalTasks)}</div></div>
      </div>

      <table>
        <thead>
          <tr><th>Kết thúc</th><th>Cuộc họp</th><th>Người tạo</th><th>Tham dự</th><th>Trạng thái</th><th>Tasks</th></tr>
        </thead>
        <tbody>
          {rows.map((m) => {
            const st = STATUS_LABEL[m.status] ?? { text: m.status, cls: "off" };
            return (
              <tr key={m.id}>
                <td>{m.end_at ? String(m.end_at).slice(0, 16).replace("T", " ") : "—"}</td>
                <td>{m.title || "(không tiêu đề)"}</td>
                <td>{m.creator_name || "—"}</td>
                <td>{Array.isArray(m.attendees) ? m.attendees.length : 0}</td>
                <td><span className={`badge ${st.cls}`}>{st.text}</span></td>
                <td>{m.tasks_created || ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}
