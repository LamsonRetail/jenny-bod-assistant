import { sb } from "@/lib/db";
import { saveConfig, deleteConfig } from "./actions";

export const dynamic = "force-dynamic";

export default async function Configs() {
  const { data: configs } = await sb().from("configs").select("*").order("key");

  return (
    <>
      <h2>Configs</h2>
      <p className="hint">
        Chi tiết hay thay đổi của skills nằm ở đây (JSON). Agent đọc config mới
        ở tin nhắn kế tiếp. Lưu ý: value phải là JSON hợp lệ — sai sẽ không lưu.
      </p>

      {(configs ?? []).map((c) => (
        <div className="skill" key={c.key}>
          <div className="head">
            <b className="mono">{c.key}</b>
            <form className="inline" action={deleteConfig}>
              <input type="hidden" name="key" value={c.key} />
              <button className="danger">Xóa</button>
            </form>
          </div>
          <form action={saveConfig}>
            <input type="hidden" name="key" value={c.key} />
            <input type="text" name="description" defaultValue={c.description ?? ""} placeholder="Mô tả" />
            <div style={{ height: 8 }} />
            <textarea name="value" defaultValue={JSON.stringify(c.value, null, 2)} />
            <div className="row"><button>Lưu</button></div>
          </form>
        </div>
      ))}

      <h3>Thêm config mới</h3>
      <div className="skill">
        <form action={saveConfig}>
          <input type="text" name="key" placeholder="key (vd: bq_data_dictionary)" />
          <div style={{ height: 8 }} />
          <input type="text" name="description" placeholder="Mô tả" />
          <div style={{ height: 8 }} />
          <textarea name="value" placeholder='{"status": "..."}' />
          <div className="row"><button>Tạo config</button></div>
        </form>
      </div>
    </>
  );
}
