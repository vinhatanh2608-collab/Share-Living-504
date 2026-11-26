import { useRef, useState, type FormEvent } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../lib/api";
import { categories, localDate, type Group, type Member, type Summary, useAction, useResource, vnd } from "../lib/living";

const colors = ["#1d796b", "#e38a58", "#4776b5", "#9578ad", "#cead45", "#638453"];
type Entry = { id: number; category: string; note: string; type: "EXPENSE" | "INCOME"; amount: number; spent_at: string };
type Shared = { id: number; title: string; category: string; amount: number };

function CategorySelect() {
  return <label>Category<select name="category">{categories.map(category => <option key={category}>{category}</option>)}</select></label>;
}

export default function OverviewAnalyticsPage({ group, revision, reload, configure, userId }: {
  group?: Group; revision: number; reload: () => void; configure: () => void; userId: number;
}) {
  const [period, setPeriod] = useState("month");
  const [scope, setScope] = useState("combined");
  const [custom, setCustom] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [editing, setEditing] = useState<Entry>();
  const pending = useRef<{ body: string; id: string }>();
  const report = useResource<Summary>(`/analytics/summary?period=${period}&scope=${scope}`, revision);
  const personal = useResource<Entry[]>("/personal-expenses", revision);
  const directory = useResource<Member[]>(group ? `/groups/${group.id}/members` : null, revision);
  const shared = useResource<Shared[]>(group ? `/groups/${group.id}/expenses` : null, revision);
  const action = useAction(reload);
  const summary = report.data;
  const eligible = directory.data?.filter(member => member.status === "APPROVED" && member.qr) ?? [];

  function personalSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    const payload = { ...data, amount: Number(data.amount) };
    void action.run(async () => {
      if (editing) await api.put(`/personal-expenses/${editing.id}`, payload);
      else await api.post("/personal-expenses", payload);
      setEditing(undefined);
      form.reset();
    });
  }

  function groupSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!group || !selected.length) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const payload = { title: fields.get("title"), category: fields.get("category"), amount: Number(fields.get("amount")),
      participants: selected, custom_shares: custom ? Object.fromEntries(selected.map(id => [id, Number(fields.get(`share-${id}`))])) : null };
    const body = JSON.stringify(payload);
    if (pending.current?.body !== body) pending.current = { body, id: crypto.randomUUID() };
    const request_id = pending.current.id;
    void action.run(async () => {
      await api.post(`/groups/${group.id}/expenses`, { ...payload, request_id });
      pending.current = undefined;
      setSelected([]);
      form.reset();
    });
  }

  function budgetSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    void action.run(async () => {
      await api.put("/budgets", { category: data.get("category"), monthly_limit: Number(data.get("monthly_limit")),
        month: `${data.get("month")}-01` });
      form.reset();
    });
  }

  return <section className="living-stack">
    <header className="living-heading"><h1>Spending overview</h1><div className="living-buttons">
      <label>Range <select value={period} onChange={e => setPeriod(e.target.value)}>
        <option value="week">Last 7 days</option><option value="month">This month</option><option value="year">This year</option>
      </select></label>
      <label>Scope <select value={scope} onChange={e => setScope(e.target.value)}>
        <option value="combined">Combined</option><option value="personal">Personal</option><option value="group">Group shares</option>
      </select></label>
    </div></header>
    {[report.error, action.error, personal.error, directory.error, shared.error].filter(Boolean).map((error, i) =>
      <p key={i} role="alert" className="error">{error}</p>)}
    {report.loading && !summary && <p role="status">Loading spending overview…</p>}
    {summary && <>
      <div className="living-metrics">
        {([["Personal spending", summary.personal], ["Group share", summary.group], ["Income", summary.income],
          ["I owe", summary.owing], ["Owed to me", summary.owed]] as const).map(([label, value]) =>
          <div className="living-card" key={label}><p>{label}</p><strong>{vnd(value)}</strong></div>)}
      </div>
      <div className="living-grid">
        <div className="living-card"><h2>Spending by category</h2>
          {summary.categories.length ? <ResponsiveContainer width="100%" height={240}>
            <PieChart><Pie data={summary.categories} dataKey="amount" nameKey="category" innerRadius={47} outerRadius={78}>
              {summary.categories.map((row, i) => <Cell key={row.category} fill={colors[i % colors.length]} />)}
            </Pie><Tooltip formatter={value => vnd(Number(value))} /><Legend /></PieChart>
          </ResponsiveContainer> : <p>No spending in this range.</p>}
        </div>
        <div className="living-card"><h2>Spending and budget</h2>
          <ResponsiveContainer width="100%" height={240}><BarChart data={summary.trend}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="date" tickFormatter={value => String(value).slice(5)} />
            <YAxis tickFormatter={value => new Intl.NumberFormat("vi-VN", { notation: "compact" }).format(Number(value))} />
            <Tooltip formatter={value => vnd(Number(value))} /><Legend />
            <Bar dataKey="spending" name="Spending" fill={colors[0]} /><Bar dataKey="budget" name="Budget" fill={colors[1]} />
          </BarChart></ResponsiveContainer>
        </div>
      </div>
      {scope === "combined" && <div className="living-card"><h2>Personal spending and group share</h2>
        <ResponsiveContainer width="100%" height={170}>
          <BarChart layout="vertical" data={[{ name: "Personal", amount: summary.personal },
            { name: "Group share", amount: summary.group }]}>
            <XAxis type="number" tickFormatter={value => new Intl.NumberFormat("vi-VN", { notation: "compact" }).format(Number(value))} />
            <YAxis type="category" dataKey="name" width={90} />
            <Tooltip formatter={value => vnd(Number(value))} />
            <Bar dataKey="amount" name="Spending" fill={colors[0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>}
      {!!summary.budgets.length && <div className="living-card"><h2>Monthly limits</h2>
        {summary.budgets.map(row => <div className="living-budget" key={row.category}>
          <label>{row.category} · {vnd(row.spent)} of {vnd(row.monthly_limit)}
            <progress value={Math.min(row.spent, row.monthly_limit)} max={row.monthly_limit} />
          </label>
        </div>)}
      </div>}
    </>}
    <div className="living-grid">
      <form className="living-card form" key={editing?.id ?? "new"} onSubmit={personalSubmit}>
        <h2>{editing ? "Edit personal transaction" : "Personal transaction"}</h2>
        <label>Type<select name="type" defaultValue={editing?.type}><option>EXPENSE</option><option>INCOME</option></select></label>
        <label>Category<select name="category" defaultValue={editing?.category}>{categories.map(category =>
          <option key={category}>{category}</option>)}</select></label>
        <label>Amount (VND)<input name="amount" type="number" required min={1} max={10 ** 12} defaultValue={editing?.amount} /></label>
        <label>Date<input name="spent_at" type="date" required defaultValue={editing?.spent_at ?? localDate()} /></label>
        <label>Note<input name="note" maxLength={250} defaultValue={editing?.note} /></label>
        <button className="primary" disabled={action.busy}>Save transaction</button>
        {editing && <button type="button" className="secondary" onClick={() => setEditing(undefined)}>Cancel</button>}
      </form>
      <form className="living-card form" onSubmit={budgetSubmit}>
        <h2>Set a monthly limit</h2><CategorySelect />
        <label>Limit (VND)<input name="monthly_limit" type="number" required min={1} max={10 ** 12} /></label>
        <label>Month<input name="month" type="month" required defaultValue={localDate().slice(0, 7)} /></label>
        <button className="primary" disabled={action.busy}>Save limit</button>
      </form>
    </div>
    <div className="living-card"><h2>Recent personal transactions</h2>
      {personal.data?.map(entry => <div className="living-row" key={entry.id}>
        <span>{entry.note || entry.category} · {entry.spent_at} · {entry.type === "INCOME" ? "+" : "−"}{vnd(entry.amount)}</span>
        <div className="living-buttons"><button className="secondary" onClick={() => setEditing(entry)}>Edit</button>
          <button className="secondary" disabled={action.busy} onClick={() => {
            if (window.confirm("Delete this transaction?")) void action.run(() => api.delete(`/personal-expenses/${entry.id}`));
          }}>Delete</button></div>
      </div>)}
      {personal.data?.length === 0 && <p>No personal transactions yet.</p>}
    </div>
    <div className="living-grid">
      <div className="living-card">
        <h2>Shared expense {group && `· ${group.name}`}</h2>
        {!group && <p>Select an approved group to log shared expenses.</p>}
        {group && !group.qr_ready && <><p role="status">Configure your group VietQR to log a shared expense.</p>
          <button className="secondary" onClick={configure}>Set up VietQR</button></>}
        {group?.qr_ready && <form className="form" onSubmit={groupSubmit}>
          <label>Title<input name="title" required maxLength={200} /></label><CategorySelect />
          <label>Amount (VND)<input name="amount" type="number" required min={1} max={10 ** 12} /></label>
          <label>Split method<select value={custom ? "custom" : "equal"} onChange={event => setCustom(event.target.value === "custom")}>
            <option value="equal">Equal</option><option value="custom">Custom amounts</option></select></label>
          <fieldset><legend>Participants with VietQR</legend>{eligible.map(member => <div className="living-check" key={member.id}>
            <label><input type="checkbox" checked={selected.includes(member.id)} onChange={event =>
              setSelected(ids => event.target.checked ? [...ids, member.id] : ids.filter(id => id !== member.id))} />
              {member.name}{member.id === userId ? " (you)" : ""}</label>
            {custom && selected.includes(member.id) && <input aria-label={`Share for ${member.name}`}
              name={`share-${member.id}`} type="number" required min={1} max={10 ** 12} />}
          </div>)}</fieldset>
          <button className="primary" disabled={action.busy || !selected.length}>Save shared expense</button>
        </form>}
      </div>
      {group && <div className="living-card"><h2>Recent group expenses</h2>
        {shared.data?.map(entry => <div className="living-row" key={entry.id}><span>{entry.title}</span><strong>{vnd(entry.amount)}</strong></div>)}
        {shared.data?.length === 0 && <p>No group expenses yet.</p>}
      </div>}
    </div>
  </section>;
}
