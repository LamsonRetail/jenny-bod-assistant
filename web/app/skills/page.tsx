import { sb } from "@/lib/db";
import { saveSkill, deleteSkill, toggleSkill } from "./actions";

export const dynamic = "force-dynamic";

export default async function Skills() {
  const { data: skills } = await sb().from("skills").select("*").order("name");

  return (
    <>
      <h2>Skills</h2>
      <p className="hint">
        Skill là năng lực <b>general</b> (markdown). Chi tiết hay thay đổi để ở
        tab Configs. Agent nạp skill mới ở tin nhắn kế tiếp — không cần deploy.
      </p>

      {(skills ?? []).map((s) => (
        <div className="skill" key={s.id}>
          <div className="head">
            <b className="mono">{s.name}</b>
            <span>
              <span className={`badge ${s.enabled ? "ok" : "off"}`}>{s.enabled ? "đang bật" : "đang tắt"}</span>{" "}
              <form className="inline" action={toggleSkill}>
                <input type="hidden" name="id" value={s.id} />
                <input type="hidden" name="to" value={String(!s.enabled)} />
                <button className="ghost">{s.enabled ? "Tắt" : "Bật"}</button>
              </form>{" "}
              <form className="inline" action={deleteSkill}>
                <input type="hidden" name="id" value={s.id} />
                <button className="danger">Xóa</button>
              </form>
            </span>
          </div>
          <form action={saveSkill}>
            <input type="hidden" name="id" value={s.id} />
            <input type="hidden" name="name" value={s.name} />
            {s.enabled ? <input type="hidden" name="enabled" value="on" /> : null}
            <input type="text" name="description" defaultValue={s.description} />
            <div style={{ height: 8 }} />
            <textarea name="content_md" defaultValue={s.content_md} />
            <div className="row"><button>Lưu</button></div>
          </form>
        </div>
      ))}

      <h3>Thêm skill mới</h3>
      <div className="skill">
        <form action={saveSkill}>
          <input type="text" name="name" placeholder="tên-skill (slug, vd: market-report)" />
          <div style={{ height: 8 }} />
          <input type="text" name="description" placeholder="Mô tả 1 dòng — agent dùng để chọn skill" />
          <div style={{ height: 8 }} />
          <textarea name="content_md" placeholder="Nội dung skill (markdown, general — chi tiết để ở Configs)" />
          <div className="row">
            <label><input type="checkbox" name="enabled" defaultChecked /> Bật ngay</label>
            <button>Tạo skill</button>
          </div>
        </form>
      </div>
    </>
  );
}
