import { sb, fmtTime } from "@/lib/db";

export const dynamic = "force-dynamic";

export default async function Conversations() {
  const db = sb();
  const [{ data: convs }, { data: msgs }] = await Promise.all([
    db.from("conversations").select("*").order("created_at", { ascending: false }).limit(50),
    db.from("messages")
      .select("id,conversation_id,direction,sender_name,content,tokens_output,created_at")
      .order("created_at", { ascending: false }).limit(40),
  ]);
  const title = new Map((convs ?? []).map((c) => [c.id, c.title || c.chat_id]));

  return (
    <>
      <h2>Hội thoại</h2>
      <table>
        <thead><tr><th>Kênh</th><th>Tên</th><th>Group</th><th>Whitelist</th><th>Chat ID</th></tr></thead>
        <tbody>
          {(convs ?? []).map((c) => (
            <tr key={c.id}>
              <td>{c.channel}</td>
              <td>{c.title || "—"}</td>
              <td>{c.is_group ? "✓" : ""}</td>
              <td><span className={`badge ${c.whitelisted ? "ok" : "off"}`}>{c.whitelisted ? "đã duyệt" : "chưa"}</span></td>
              <td className="mono">{c.chat_id}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Tin nhắn gần đây</h3>
      <table>
        <thead><tr><th>Lúc</th><th>Chat</th><th>Chiều</th><th>Nội dung</th></tr></thead>
        <tbody>
          {(msgs ?? []).map((m) => (
            <tr key={m.id}>
              <td>{fmtTime(m.created_at)}</td>
              <td>{title.get(m.conversation_id) ?? "?"}</td>
              <td>{m.direction === "in" ? `→ ${m.sender_name ?? ""}` : "← Jenny"}</td>
              <td>{m.content.slice(0, 200)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
