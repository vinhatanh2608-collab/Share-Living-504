import { useRef, useState, type FormEvent } from "react";
import { api, explain } from "../lib/api";
import { money } from "../lib/format";
import type { Roommate } from "../types/api";
import Modal from "./Modal";

export default function CreateExpenseModal({ directory, userId, close, done }: {
  directory: Roommate[]; userId: number; close: () => void; done: () => void;
}) {
  const [selected, select] = useState<number[]>(directory.map(u => u.id));
  const [mode, setMode] = useState<"equal" | "custom">("equal");
  const [title, setTitle] = useState("");
  const [amount, setAmount] = useState("");
  const [due, setDue] = useState("");
  const [shares, setShares] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const last = useRef<{body: string; key: string}>();
  const sum = selected.reduce((v, id) => v + Number(shares[id] || 0), 0);
  const count = selected.length;
  const equal = count ? Math.floor(Number(amount || 0) / count) : 0;
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError("");
    const total = Number(amount);
    if (!title.trim() || !Number.isSafeInteger(total) || total < count || !count || total > 1e12 || !due)
      return setError("Enter a title, valid whole-VND amount, due date, and participants.");
    if (!selected.some(id => id !== userId)) return setError("Select at least one other roommate.");
    if (mode === "custom" && (sum !== total || selected.some(id => !Number.isSafeInteger(Number(shares[id])) || Number(shares[id]) <= 0)))
      return setError("Each selected roommate needs a positive whole-VND share; shares must add up to the total.");
    const body = { title: title.trim(), amount: total, participants: [...selected].sort((a,b) => a-b),
      due_at: new Date(due).toISOString(), ...(mode === "custom" ? {
        custom_shares: Object.fromEntries(selected.map(id => [id, Number(shares[id])])) } : {}) };
    const serialized = JSON.stringify(body);
    if (last.current?.body !== serialized) last.current = { body: serialized, key: crypto.randomUUID() };
    setBusy(true);
    try { await api.post("/finance/expenses", { ...body, request_id: last.current.key }); done(); close(); }
    catch (e) { setError(explain(e)); } finally { setBusy(false); }
  }
  return <Modal title="Create expense" close={close} busy={busy}><form className="form" onSubmit={submit}>
    <label>Expense name<input required maxLength={200} value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. November electricity"/></label>
    <div className="grid grid-cols-2 gap-3">
      <label>Total (VND)<input required type="number" min="1" max="1000000000000" value={amount} onChange={e => setAmount(e.target.value)} /></label>
      <label>Due date and time<input required type="datetime-local" value={due} onChange={e => setDue(e.target.value)}/></label>
    </div>
    <label>Split method<select value={mode} onChange={e => setMode(e.target.value === "custom" ? "custom" : "equal")}><option value="equal">Equal split</option><option value="custom">Custom amounts</option></select></label>
    <div className="list compact">{directory.map(u => <div className="split-row" key={u.id}>
      <label className="check"><input type="checkbox" checked={selected.includes(u.id)} onChange={() => select(s => s.includes(u.id) ? s.filter(i => i !== u.id) : [...s, u.id])}/>{u.name}{u.id === userId ? " (you)" : ""}</label>
      {mode === "custom" && selected.includes(u.id) && <input aria-label={`Share for ${u.name}`} type="number" min="1" step="1" value={shares[u.id] ?? ""} onChange={e => setShares({ ...shares, [u.id]: e.target.value })}/>}
    </div>)}</div>
    <p className="muted">{mode === "custom" ? `Allocated: ${money(sum)}` : `${count} selected · about ${money(equal)} each`}. Your own share does not create a debt.</p>
    {error && <p role="alert" className="error">{error}</p>}
    <button className="primary" disabled={busy || !directory.length}>{busy ? "Saving…" : "Create expense"}</button>
  </form></Modal>;
}
