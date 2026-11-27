import { useRef, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { categories, type Category, type Member, useAction, useResource, vnd } from "../lib/living";

const labels: Record<Category, string> = {
  FOOD: "Ăn uống", TRANSPORT: "Đi lại", HOUSING: "Nhà ở", STUDY: "Học tập",
  ENTERTAINMENT: "Giải trí", OTHER: "Khác",
};
const states = { paid: "Đã góp tiền", unpaid: "Chưa góp tiền", excluded: "Không liên quan" } as const;
type Status = keyof typeof states;
type Expense = { id: number; title: string; category: Category; amount: number; creditor_id: number;
  created_at: string; funded_by_pool?: boolean; participants?: { user_id: number; status: Status; paid_amount: number; share_amount: number }[] | null };
type Template = { id: number; title: string; category: Category; default_amount: number; unit_price: number | null; last_meter_reading: number | null; participants_preset_json: number[] };
type Props = { owner?: boolean; groupId: number; userId: number; members: Member[]; revision: number; reload: () => void };

export default function GroupExpenses(props: Props) {
  return <ExpensePanel key={props.groupId} {...props} />;
}

function ExpensePanel({ groupId, userId, members, revision, reload, owner }: Props) {
  const endpoint = `/groups/${groupId}/expenses`;
  const ledger = useResource<Expense[]>(endpoint, revision);
  const action = useAction(reload);
  const request = useRef({ payload: "", id: "" });
  const path = `/groups/${groupId}/expense-templates`;
  const templates = useResource<Template[]>(path, revision);
  const [template, setTemplate] = useState<number>();
  const [pool, setPool] = useState(false);
  const [meter, setMeter] = useState(false);
  const [readings, setReadings] = useState(["", "", ""]);
  const [custom, setCustom] = useState(false);
  const [amount, setAmount] = useState("");
  const [statuses, setStatuses] = useState<Record<number, Status>>({});
  const [contributions, setContributions] = useState<Record<number, string>>({});
  const [customValues, setCustomValues] = useState<Record<number, string>>({});
  const [message, setMessage] = useState("");
  const approved = members.filter(member => member.status === "APPROVED").sort((a, b) => a.id - b.id);
  const statusOf = (id: number, hasQr: boolean): Status => pool ? (statuses[id] === "excluded" ? "excluded" : "unpaid") : !hasQr ? "excluded" :
    statuses[id] ?? (id === userId ? "paid" : "unpaid");
  const active = approved.filter(member => statusOf(member.id, Boolean(member.qr)) !== "excluded");
  const ids = active.map(member => member.id);
  const payers = active.filter(member => statusOf(member.id, true) === "paid");
  const [old, next, price] = readings.map(Number);
  const total = meter ? (next - old) * price : Number(amount);
  const paidBy = (id: number) => payers.some(member => member.id === id) ?
    (payers.length === 1 ? total : Number(contributions[id] || 0)) : 0;
  const contributed = payers.reduce((sum, member) => sum + paidBy(member.id), 0);
  const shares = ids.map((id, index) => custom ? Number(customValues[id] || 0) :
    Math.floor(total / ids.length) + Number(index < total % ids.length));
  const difference = total - shares.reduce((sum, share) => sum + share, 0);
  const valid = ids.length > 0 && Number.isSafeInteger(total) && total > 0 && total <= 10**12 && (!meter || readings.every(Boolean)) && (pool || (payers.length > 0 && contributed === total && payers.every(member => paidBy(member.id) > 0))) &&
    shares.every(share => Number.isInteger(share) && share >= 0) && difference === 0;
  const name = (id: number) => members.find(member => member.id === id)?.name ?? `Thành viên #${id}`;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const title = String(values.get("title")).trim();
    const payload = { title: meter ? `${title}: ${old.toLocaleString("vi-VN")} → ${next.toLocaleString("vi-VN")} (${next-old} × ${vnd(price)})` : title,
      category: values.get("category"), funded_by_pool: pool, template_id: template,
      ...(meter ? { old_reading: old, new_reading: next, unit_price: price } : {}),
      amount: total, participants: approved.map(member => ({
        user_id: String(member.id), status: statusOf(member.id, Boolean(member.qr)), paid_amount: paidBy(member.id),
        ...(custom ? { share_amount: shares[ids.indexOf(member.id)] ?? 0 } : {}),
      })) };
    const fingerprint = JSON.stringify(payload);
    const saved = await action.run(async () => {
      if (request.current.payload !== fingerprint) request.current = { payload: fingerprint, id: crypto.randomUUID() };
      await api.post(endpoint, { ...payload, request_id: request.current.id });
    });
    if (saved) {
      form.reset(); setTemplate(undefined); setPool(false); setMeter(false); setReadings(["", "", ""]); setStatuses({}); setContributions({}); setAmount(""); setCustom(false); setCustomValues({});
      request.current = { payload: "", id: "" };
      setMessage("Đã ghi vào sổ chi tiêu nhóm.");
    }
  }

  return <div className="living-stack group-expenses">
    <form className="living-card form" onSubmit={submit} onChange={() => setMessage("")}>
      <h2>Thêm chi tiêu nhóm</h2>
      <label>Mẫu chi tiêu nhanh<select value={template ?? ""} onChange={event => {
        const row = templates.data?.find(item => item.id === Number(event.target.value));
        setTemplate(row?.id);
        if (!row) return;
        const form = event.currentTarget.form!;
        (form.elements.namedItem("title") as HTMLInputElement).value = row.title;
        (form.elements.namedItem("category") as HTMLSelectElement).value = row.category;
        setAmount(String(row.default_amount)); setMeter(Boolean(row.unit_price));
        setReadings([String(row.last_meter_reading ?? ""), "", String(row.unit_price ?? "")]);
        setCustom(false); setCustomValues({}); setContributions({});
        setStatuses(row.participants_preset_json.length ? Object.fromEntries(approved.map(member =>
          [member.id, row.participants_preset_json.includes(member.id) ? (member.id === userId ? "paid" : "unpaid") : "excluded"])) : {});
      }}><option value="">Chọn mẫu</option>{templates.data?.map(row => <option key={row.id} value={row.id}>{row.title}</option>)}</select></label>
      {templates.error && <p role="alert" className="error">{templates.error}</p>}
      <div className="living-buttons">
        <button type="button" className="secondary" disabled={action.busy || !valid} onClick={event => {
          const values = new FormData(event.currentTarget.form!);
          void action.run(async () => {
            await api.post(path, { title: values.get("title"), category: values.get("category"), default_amount: total,
              unit_price: meter ? price : null, last_meter_reading: meter ? next : null, participants_preset_json: ids });
            setMessage("Đã lưu mẫu chi tiêu.");
          });
        }}>Lưu làm mẫu</button>
        {owner && template && <button type="button" className="secondary" disabled={action.busy} onClick={() => {
          if (window.confirm("Xóa mẫu chi tiêu đang chọn?")) void action.run(async () => {
            await api.delete(`${path}/${template}`); setTemplate(undefined);
          });
        }}>Xóa mẫu</button>}
      </div>
      <label className="living-check"><input type="checkbox" checked={pool} onChange={event => setPool(event.target.checked)} />Trích từ quỹ chung (không tính nợ cá nhân)</label>
      <details><summary>Tính theo chỉ số điện/nước</summary>
        <label className="living-check"><input type="checkbox" checked={meter} onChange={event => setMeter(event.target.checked)} />Dùng chỉ số để tính tiền</label>
        {meter && ["Chỉ số cũ", "Chỉ số mới", "Đơn giá (đồng)"].map((label, index) =>
          <label key={label}>{label}<input required type="number" min={index === 2 ? 1 : 0} max={10**12} value={readings[index]}
            onChange={event => setReadings(readings.map((value, i) => i === index ? event.target.value : value))} /></label>)}
      </details>
      <label>Nội dung<input name="title" required pattern=".*\S.*" maxLength={130} placeholder="Ví dụ: Tiền điện tháng này" /></label>
      <div className="living-grid">
        <label>Số tiền (đồng)<input type="number" inputMode="numeric" required min="1" max="1000000000000"
          readOnly={meter} value={meter ? (total > 0 ? total : "") : amount} onChange={event => setAmount(event.target.value)} /></label>
        <label>Danh mục<select name="category">{categories.map(category =>
          <option key={category} value={category}>{labels[category]}</option>)}</select></label>
      </div>
      <label>Cách chia<select value={custom ? "custom" : "equal"} onChange={event => setCustom(event.target.value === "custom")}>
        <option value="equal">Chia đều</option><option value="custom">Nhập số tiền từng người</option></select></label>
      <fieldset><legend>Người cùng chia</legend>
        {approved.map(member => {
          const index = ids.indexOf(member.id);
          const status = statusOf(member.id, Boolean(member.qr));
          return <fieldset key={member.id}><legend>{member.name}{!pool && !member.qr && " (chưa có VietQR)"}</legend>
            <div className="living-buttons">{(pool ? ["unpaid", "excluded"] as const : Object.keys(states) as Status[]).map(option =>
              <button type="button" key={option} className={status === option ? "primary" : "secondary"}
                aria-pressed={status === option} disabled={!pool && !member.qr && option !== "excluded"}
                onClick={() => { setStatuses({ ...statuses, [member.id]: option }); setMessage(""); }}>{pool && option === "unpaid" ? "Có tham gia" : states[option]}</button>)}</div>
            {status === "paid" && <label>Số tiền đã góp (đồng)<input type="number" required min="1"
              max={total || undefined} readOnly={payers.length === 1} value={payers.length === 1 ? String(total || "") : contributions[member.id] ?? ""}
              onChange={event => setContributions({ ...contributions, [member.id]: event.target.value })} /></label>}
            {index >= 0 && (custom ? <input type="number" aria-label={`Phần của ${member.name} (đồng)`} min="0"
              max={total || undefined} required value={customValues[member.id] ?? ""}
              onChange={event => setCustomValues({ ...customValues, [member.id]: event.target.value })} /> : <span>Phần phải chia: {vnd(shares[index])}</span>)}
          </fieldset>;
        })}
      </fieldset>
      <p aria-live="polite">Chia cho {ids.length}/{approved.length} thành viên
        {!custom && ids.length > 0 && ` • Mỗi người chịu: ${vnd(Math.min(...shares))}${total % ids.length ? ` – ${vnd(Math.max(...shares))}` : ""}`}
        {" • "}{pool ? "Chi từ quỹ chung" : !payers.length ? "Chọn người đã góp tiền" : contributed === total && total > 0 ? "Tổng đã góp khớp hóa đơn" :
          `Đã góp ${vnd(contributed)} / ${vnd(total)}`}</p>
      {custom && difference !== 0 && <p role="status">{difference > 0 ? "Còn cần chia" : "Đã chia vượt"} {vnd(Math.abs(difference))}</p>}
      {action.error && <p className="error" role="alert">{action.error}</p>}
      {message && <p role="status">{message}</p>}
      <button className="primary" disabled={!valid || action.busy}>Lưu khoản chi</button>
    </form>
    <section className="living-card"><h2>Sổ chi tiêu nhóm</h2>
      {ledger.error && <p className="error" role="alert">{ledger.error}</p>}
      {ledger.loading && <p role="status">Đang tải sổ chi tiêu…</p>}
      {ledger.data?.length === 0 && <p>Chưa có khoản chi chung.</p>}
      {ledger.data?.map(row => <details key={row.id}>
        <summary><strong>{row.title}</strong> · {vnd(row.amount)}<br />
          {labels[row.category]} · {row.funded_by_pool ? "Chi từ quỹ chung" : row.participants?.filter(p => p.status === "paid").map(p => name(p.user_id)).join(", ") || "Chưa có thông tin người góp"} · {new Date(row.created_at + (/(Z|[+-]\d\d:\d\d)$/i.test(row.created_at) ? "" : "Z"))
            .toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", dateStyle: "short", timeStyle: "short" })}</summary>
        {!row.participants?.length && <p>Chưa có chi tiết chia chi phí.</p>}
        <ul>{row.participants?.map(p => <li key={p.user_id}>{name(p.user_id)} · {row.funded_by_pool && p.status !== "excluded" ? "Có tham gia" : states[p.status]}
          {p.status !== "excluded" && ` · ${row.funded_by_pool ? "Phần phân bổ" : `Góp ${vnd(p.paid_amount)} · Chịu`} ${vnd(p.share_amount)}`}</li>)}</ul>
      </details>)}
    </section>
  </div>;
}
