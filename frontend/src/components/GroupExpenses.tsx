import { useRef, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { categories, type Category, type Member, useAction, useResource, vnd } from "../lib/living";

const labels: Record<Category, string> = {
  FOOD: "Ăn uống", TRANSPORT: "Đi lại", HOUSING: "Nhà ở", STUDY: "Học tập",
  ENTERTAINMENT: "Giải trí", OTHER: "Khác",
};
type Expense = { id: number; title: string; category: Category; amount: number; creditor_id: number;
  created_at: string; shares: { user_id: number; amount: number }[] };
type Props = { groupId: number; userId: number; members: Member[]; revision: number; reload: () => void };

export default function GroupExpenses(props: Props) {
  return <ExpensePanel key={props.groupId} {...props} />;
}

function ExpensePanel({ groupId, userId, members, revision, reload }: Props) {
  const endpoint = `/groups/${groupId}/expenses`;
  const ledger = useResource<Expense[]>(endpoint, revision);
  const action = useAction(reload);
  const request = useRef({ payload: "", id: "" });
  const [custom, setCustom] = useState(false);
  const [amount, setAmount] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [customValues, setCustomValues] = useState<Record<number, string>>({});
  const [message, setMessage] = useState("");
  const eligible = members.filter(member => member.status === "APPROVED" && member.qr);
  const payerReady = eligible.some(member => member.id === userId);
  const ids = eligible.map(member => member.id).filter(id => selected.includes(id)).sort((a, b) => a - b);
  const total = Number(amount);
  const shares = ids.map((id, index) => custom ? Number(customValues[id] || 0) :
    Math.floor(total / ids.length) + Number(index < total % ids.length));
  const difference = total - shares.reduce((sum, share) => sum + share, 0);
  const valid = payerReady && ids.length > 0 && ids.length <= 25 && total > 0 &&
    shares.every(share => Number.isInteger(share) && share > 0) && difference === 0;
  const name = (id: number) => members.find(member => member.id === id)?.name ?? `Thành viên #${id}`;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const payload = { title: String(values.get("title")).trim(), category: values.get("category"),
      amount: total, participants: ids,
      custom_shares: custom ? Object.fromEntries(ids.map((id, i) => [id, shares[i]])) : null };
    const fingerprint = JSON.stringify(payload);
    const saved = await action.run(async () => {
      if (request.current.payload !== fingerprint) request.current = { payload: fingerprint, id: crypto.randomUUID() };
      await api.post(endpoint, { ...payload, request_id: request.current.id });
    });
    if (saved) {
      form.reset(); setSelected([]); setAmount(""); setCustom(false); setCustomValues({});
      request.current = { payload: "", id: "" };
      setMessage("Đã ghi vào sổ chi tiêu nhóm.");
    }
  }

  return <div className="living-stack group-expenses">
    <form className="living-card form" onSubmit={submit} onChange={() => setMessage("")}>
      <h2>Thêm chi tiêu nhóm</h2>
      <p>Người trả tiền: <strong>{name(userId)}</strong></p>
      {!payerReady && <p role="status">Cập nhật VietQR của bạn trong nhóm để ghi khoản chi.</p>}
      <label>Nội dung<input name="title" required pattern=".*\S.*" maxLength={200} placeholder="Ví dụ: Tiền điện tháng này" /></label>
      <div className="living-grid">
        <label>Số tiền (đồng)<input type="number" inputMode="numeric" required min="1" max="1000000000000"
          value={amount} onChange={event => setAmount(event.target.value)} /></label>
        <label>Danh mục<select name="category">{categories.map(category =>
          <option key={category} value={category}>{labels[category]}</option>)}</select></label>
      </div>
      <label>Cách chia<select value={custom ? "custom" : "equal"} onChange={event => setCustom(event.target.value === "custom")}>
        <option value="equal">Chia đều</option><option value="custom">Nhập số tiền từng người</option></select></label>
      <fieldset><legend>Người cùng chia</legend>
        {members.filter(member => member.status === "APPROVED").map(member => {
          const index = ids.indexOf(member.id);
          return <div className="living-row" key={member.id}>
            <label><input type="checkbox" checked={index >= 0} disabled={!member.qr || (index < 0 && ids.length >= 25)}
              onChange={event => setSelected(event.target.checked ? [...ids, member.id] : ids.filter(id => id !== member.id))} />
              {member.name}{!member.qr && " (chưa có VietQR)"}</label>
            {index >= 0 && (custom ? <input type="number" aria-label={`Phần của ${member.name} (đồng)`} min="1"
              max={total || undefined} required value={customValues[member.id] ?? ""}
              onChange={event => setCustomValues({ ...customValues, [member.id]: event.target.value })} /> : vnd(shares[index]))}
          </div>;
        })}
      </fieldset>
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
          {labels[row.category]} · {name(row.creditor_id)} đã trả · {new Date(row.created_at + (/(Z|[+-]\d\d:\d\d)$/i.test(row.created_at) ? "" : "Z"))
            .toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", dateStyle: "short", timeStyle: "short" })}</summary>
        <ul>{row.shares.map(share => <li key={share.user_id}>{name(share.user_id)} · {vnd(share.amount)}</li>)}</ul>
      </details>)}
    </section>
  </div>;
}
