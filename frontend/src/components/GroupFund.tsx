import { useState } from "react";
import Modal from "./Modal";
import { api } from "../lib/api";
import { useAction, useResource, vnd } from "../lib/living";

type Fund = { balance: number; entries: { id: number; user_id: number; amount: number;
  tx_type: "deposit" | "expense_deduction" | "adjustment"; note: string | null; created_at: string }[] };

const labels = { deposit: "Nạp quỹ", expense_deduction: "Chi từ quỹ", adjustment: "Điều chỉnh" };

export default function GroupFund({ groupId, revision, reload, names }: {
  groupId: number; revision: number; reload: () => void; names: Map<number, string>;
}) {
  const path = `/groups/${groupId}`;
  const fund = useResource<Fund>(`${path}/fund`, revision);
  const action = useAction(reload);
  const [panel, setPanel] = useState<"deposit" | "history">();
  const [period, setPeriod] = useState(new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit",
  }).format(new Date()));
  async function download(format: "xlsx" | "csv") {
    await action.run(async () => {
      const { data } = await api.get<Blob>(`${path}/monthly-report/export`, {
        params: { period, format }, responseType: "blob",
      });
      const url = URL.createObjectURL(data);
      const link = document.createElement("a");
      link.href = url;
      link.download = `quyet-toan-${groupId}-${period}.${format}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  return <section className="living-card living-stack">
    <h2>Quỹ chung nhóm</h2>
    {fund.data && <p>Số dư: <strong>{vnd(fund.data.balance)}</strong></p>}
    {fund.loading && <p role="status">Đang tải số dư quỹ…</p>}
    {fund.error && <p className="error" role="alert">{fund.error}</p>}
    <div className="living-buttons">
      <button className="primary" onClick={() => setPanel("deposit")}>Nạp quỹ</button>
      <button className="secondary" onClick={() => setPanel("history")}>Lịch sử quỹ</button>
    </div>
    <form className="living-stack" onSubmit={event => { event.preventDefault(); void download("xlsx"); }}>
      <h2>Báo cáo quyết toán tháng</h2>
      <label>Tháng báo cáo<input type="month" required value={period} onChange={event => setPeriod(event.target.value)} /></label>
      <div className="living-buttons">
        <button className="secondary" disabled={!period || action.busy}>Tải Excel</button>
        <button type="button" className="secondary" disabled={!period || action.busy} onClick={() => void download("csv")}>Tải CSV</button>
      </div>
    </form>
    {!panel && action.error && <p className="error" role="alert">{action.error}</p>}
    {panel === "deposit" && <Modal title="Nạp quỹ" close={() => setPanel(undefined)} busy={action.busy}>
      <form className="form" onSubmit={event => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        void action.run(async () => {
          await api.post(`${path}/fund/deposit`, { amount: Number(data.get("amount")), note: data.get("note") });
          setPanel(undefined);
        });
      }}>
        <label>Số tiền (đồng)<input name="amount" type="number" inputMode="numeric" required min={1} max={10**12} /></label>
        <label>Ghi chú<textarea name="note" maxLength={250} /></label>
        {action.error && <p className="error" role="alert">{action.error}</p>}
        <button className="primary" disabled={action.busy}>Ghi khoản nạp</button>
      </form>
    </Modal>}
    {panel === "history" && <Modal title="Lịch sử quỹ" close={() => setPanel(undefined)}>
      <div className="form">{fund.data?.entries.length === 0 && <p>Chưa có giao dịch quỹ.</p>}
        {fund.error && <p className="error" role="alert">{fund.error}</p>}
        {fund.data?.entries.map(row => <article key={row.id} className="living-row">
          <div><strong>{labels[row.tx_type]} · {vnd(row.amount)}</strong>
            <p>{names.get(row.user_id) ?? `Thành viên #${row.user_id}`} · {new Date(row.created_at + "Z").toLocaleString("vi-VN")}</p>
            {row.note && <p>{row.note}</p>}</div>
        </article>)}
      </div>
    </Modal>}
  </section>;
}
