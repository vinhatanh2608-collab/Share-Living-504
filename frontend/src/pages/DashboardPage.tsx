import { ArrowDownLeft, ArrowUpRight, Bell, CircleAlert, ClipboardCheck, Plus, RefreshCw, Wallet } from "lucide-react";
import { useMemo, useState } from "react";
import CreateExpenseModal from "../components/CreateExpenseModal";
import SubmitProofModal from "../components/SubmitProofModal";
import ImageLightbox from "../components/ImageLightbox";
import ProofActions from "../components/ProofActions";
import { date, isOverdue, money } from "../lib/format";
import { api, explain } from "../lib/api";
import { useRoomStore } from "../store/useRoomStore";
import type { Debt, User } from "../types/api";

export default function DashboardPage({ user }: { user: User }) {
  const { roommates, debts, proofs, error, loading, refresh } = useRoomStore();
  const [create, setCreate] = useState(false);
  const [selected, select] = useState<number[]>([]);
  const [paying, setPaying] = useState<Debt[]>();
  const [receipt, setReceipt] = useState<number>();
  const [feedback, setFeedback] = useState("");
  const [reminding, setReminding] = useState<number>();
  const my = useMemo(() => debts.filter(d => d.debtor_id === user.id && d.status !== "paid"), [debts, user.id]);
  const received = debts.filter(d => d.creditor_id === user.id && d.status !== "paid");
  const late = my.filter(d => d.status === "open" && isOverdue(d.due_at));
  const byCreditor = new Map<number, Debt[]>();
  for (const debt of my) byCreditor.set(debt.creditor_id, [...byCreditor.get(debt.creditor_id) ?? [], debt]);
  const chosen = my.filter(d => d.status === "open" && selected.includes(d.id));
  const name = (id: number) => roommates.find(r => r.id === id)?.name ?? `Roommate #${id}`;
  const total = (rows: Debt[]) => money(rows.reduce((sum, d) => sum + d.amount, 0));
  function reload() { void refresh(user.is_owner); }
  async function remind(id: number) {
    setReminding(id);
    try { await api.post(`/finance/debts/${id}/reminder`); setFeedback(`Reminder sent for debt #${id}.`); }
    catch (e) { setFeedback(explain(e)); } finally { setReminding(undefined); }
  }

  return <div className="page">
    <header className="page-header"><div><p className="eyebrow">ROOM FINANCES</p><h1>Overview</h1><p>Track what you owe and what’s coming back.</p></div>
      <div className="actions"><button className="secondary" onClick={reload} disabled={loading}><RefreshCw size={16}/> Refresh</button>
        <button className="primary" disabled={!roommates.length} onClick={() => setCreate(true)}><Plus size={17}/> Create expense</button></div></header>
    <div className="stat-grid">
      <div className="panel stat"><span className="tile-icon blue"><Wallet size={19}/></span><small>YOU OWE</small><strong>{total(my)}</strong><p>{my.length} outstanding {my.length === 1 ? "debt" : "debts"}</p></div>
      <div className="panel stat"><span className="tile-icon teal"><ArrowDownLeft size={19}/></span><small>OWED TO YOU</small><strong>{total(received)}</strong><p>{received.length} open or in review</p></div>
      <div className="panel stat"><span className="tile-icon amber"><CircleAlert size={19}/></span><small>OVERDUE</small><strong>{late.length}</strong><p>{late.length ? `${total(late)} past due` : "No overdue payments"}</p></div>
    </div>
    {error && <p role="alert" className="error">{error}</p>}
    {feedback && <p role="status" className="notice">{feedback}</p>}
    <section className="panel section"><div className="section-head"><div><h2>My debts</h2><p>Grouped by the roommate you need to pay.</p></div>
      <button className="primary" disabled={!chosen.length} onClick={() => setPaying(chosen)}><ArrowUpRight size={16}/> Submit proof{chosen.length ? ` (${chosen.length})` : ""}</button></div>
      {!byCreditor.size && <p className="empty">{loading ? "Loading debts…" : "You have no outstanding debts."}</p>}
      {[...byCreditor].map(([id, rows]) => <div className="debt-group" key={id}><h3>{name(id)}</h3>
        {rows.map(d => <div className="debt-row" key={d.id}>
          <input type="checkbox" aria-label={`Select debt ${d.id}`} checked={chosen.some(x => x.id === d.id)}
            disabled={d.status !== "open" || (!chosen.some(x => x.id === d.id) && chosen.length >= 25)} onChange={() => select(
              chosen.some(x => x.id === d.id) ? chosen.filter(x => x.id !== d.id).map(x => x.id)
                : [...chosen.filter(x => x.creditor_id === id).map(x => x.id), d.id])}/>
          <div><strong>{d.title || `Expense #${d.expense_id}`}</strong><small>Due {date(d.due_at)} · #{d.id}</small></div>
          <span className={`pill ${d.status === "pending" ? "wait" : isOverdue(d.due_at) ? "late" : ""}`}>{d.status === "pending" ? "In review" : isOverdue(d.due_at) ? "Overdue" : "Open"}</span>
          <strong>{money(d.amount)}</strong>
        </div>)}</div>)}
    </section>
    {user.is_owner && <section className="panel section"><div className="section-head"><div><h2><ClipboardCheck size={20}/> Payment approvals</h2><p>Only you can make a decision.</p></div><span className="pill">{proofs.length} pending</span></div>
      {!proofs.length && <p className="empty">No proofs waiting for review.</p>}
      {proofs.map(p => <div className="review-row" key={p.id}><div><strong>Proof #{p.id}</strong><small>Debt #{p.debt_id} · {date(p.submitted_at)}</small></div>
        <button className="secondary" onClick={() => setReceipt(p.id)}>View receipt</button><ProofActions id={p.id} done={approved => { setFeedback(approved ? "Debt marked paid." : "Proof rejected."); reload(); }}/></div>)}
    </section>}
    <section className="panel section"><div className="section-head"><div><h2>Receivables</h2><p>Outstanding debts others owe you.</p></div></div>
      {!received.length && <p className="empty">No outstanding receivables.</p>}
      {received.map(d => <div className="review-row" key={d.id}><div><strong>{name(d.debtor_id)} · {d.title || `Debt #${d.id}`}</strong><small>{date(d.due_at)} · {d.status === "pending" ? "In review" : "Open"}</small></div><strong>{money(d.amount)}</strong>
        {d.status === "open" && isOverdue(d.due_at) && <button className="secondary" disabled={reminding !== undefined} onClick={() => void remind(d.id)}><Bell size={15}/> Remind</button>}</div>)}
    </section>
    {create && <CreateExpenseModal directory={roommates} userId={user.id} close={() => setCreate(false)} done={() => { setFeedback("Expense created."); reload(); }}/>}
    {paying && <SubmitProofModal debts={paying} close={() => setPaying(undefined)} done={() => { select([]); setFeedback("Receipt submitted for review."); reload(); }}/>}
    {receipt !== undefined && <ImageLightbox id={receipt} close={() => setReceipt(undefined)}/>}
  </div>;
}
