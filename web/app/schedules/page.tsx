import { sb, fmtTime } from "@/lib/db";
import { saveSchedule, deleteSchedule, toggleSchedule } from "./actions";

export const dynamic = "force-dynamic";

function ScheduleForm({ s }: { s?: any }) {
  return (
    <form action={saveSchedule}>
      {s ? <input type="hidden" name="id" value={s.id} /> : null}
      <div className="row">
        <input type="text" name="name" defaultValue={s?.name ?? ""} placeholder="Tên (vd: Brief sáng)" />
        <input type="text" name="cron" defaultValue={s?.cron ?? ""} placeholder="Cron giờ VN (vd: 30 7 * * 1-6)" />
      </div>
      <div className="row">
        <select name="channel" defaultValue={s?.channel ?? "lark"}
          style={{ padding: "8px 10px", border: "1px solid var(--line)", borderRadius: 8 }}>
          <option value="lark">lark</option>
          <option value="telegram">telegram</option>
        </select>
        <input type="text" name="chat_id" defaultValue={s?.chat_id ?? ""} placeholder="chat_id nhận kết quả (gõ /id trong chat)" />
      </div>
      <div style={{ height: 8 }} />
      <textarea name="prompt" defaultValue={s?.prompt ?? ""} placeholder="Yêu cầu giao cho Jenny mỗi lần chạy" />
      <div className="row">
        <label><input type="checkbox" name="enabled" defaultChecked={s ? s.enabled : true} /> Bật</label>
        <button>{s ? "Lưu" : "Tạo lịch chạy"}</button>
      </div>
    </form>
  );
}

export default async function Schedules() {
  const { data } = await sb().from("scheduled_tasks").select("*").order("created_at");

  return (
    <>
      <h2>Lịch chạy tự động</h2>
      <p className="hint">
        Cron theo giờ VN: <span className="mono">phút giờ ngày tháng thứ</span> — vd
        <span className="mono"> 30 7 * * 1-6</span> = 7:30 thứ 2→7. Kết quả gửi vào chat_id đã chọn.
        Scheduler nhận thay đổi trong ~30 giây, không cần deploy.
      </p>

      {(data ?? []).map((s) => (
        <div className="skill" key={s.id}>
          <div className="head">
            <b>{s.name} <span className="mono">({s.cron})</span></b>
            <span>
              <span className={`badge ${s.enabled ? "ok" : "off"}`}>{s.enabled ? "đang bật" : "đang tắt"}</span>{" "}
              {s.last_run_at ? <span className="hint">chạy gần nhất: {fmtTime(s.last_run_at)}</span> : null}{" "}
              <form className="inline" action={toggleSchedule}>
                <input type="hidden" name="id" value={s.id} />
                <input type="hidden" name="to" value={String(!s.enabled)} />
                <button className="ghost">{s.enabled ? "Tắt" : "Bật"}</button>
              </form>{" "}
              <form className="inline" action={deleteSchedule}>
                <input type="hidden" name="id" value={s.id} />
                <button className="danger">Xóa</button>
              </form>
            </span>
          </div>
          <ScheduleForm s={s} />
        </div>
      ))}

      <h3>Thêm lịch chạy mới</h3>
      <div className="skill"><ScheduleForm /></div>
    </>
  );
}
