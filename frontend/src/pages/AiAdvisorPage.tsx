import { useState, type FormEvent } from "react";
import SanitizedMessage from "../components/SanitizedMessage";
import { api } from "../lib/api";
import { type Advice, type Summary, useAction, useResource, vnd } from "../lib/living";

const prompts = ["Đánh giá chi tiêu tháng này", "Gợi ý lập ngân sách sinh hoạt", "Tổng hợp các khoản nợ cần nhắc"];

export default function AiAdvisorPage({ revision, reload }: { revision: number; reload: () => void }) {
  const history = useResource<Advice[]>("/ai-chat/history", revision);
  const summary = useResource<Summary>("/analytics/summary", revision);
  const action = useAction();
  const [external, setExternal] = useState(false);
  const [notice, setNotice] = useState("");
  function send(text: string, onSuccess?: () => void) {
    if (!text.trim()) return;
    void action.run(async () => {
      const { data } = await api.post<Advice>("/ai-chat/message", { text: text.trim(), use_external: external });
      history.setData(previous => [...previous ?? [], data].slice(-50));
      onSuccess?.();
    });
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    send(String(new FormData(form).get("prompt")), () => form.reset());
  }
  const snapshot = summary.data;
  return <section className="living-stack"><h1>Financial advisor</h1>
    {snapshot && (snapshot.overdue > 0 || snapshot.budgets.some(budget => budget.spent * 5 >= budget.monthly_limit * 4)) &&
      <div className="living-card" role="status">
        {snapshot.overdue > 0 && <p>Overdue unpaid debts total {vnd(snapshot.overdue)}.</p>}
        {snapshot.budgets.filter(budget => budget.spent * 5 >= budget.monthly_limit * 4).map(budget =>
          <p key={budget.category}>{budget.category} has reached {Math.floor(budget.spent * 100 / budget.monthly_limit)}% of its limit.</p>)}
      </div>}
    {(action.error || history.error || summary.error) && <p role="alert" className="error">{action.error || history.error || summary.error}</p>}
    {notice && <p role="status">{notice}</p>}
    <div className="living-buttons">{prompts.map(prompt => <button key={prompt} className="secondary"
      disabled={action.busy} onClick={() => send(prompt)}>{prompt}</button>)}</div>
    <div className="living-card living-chat">
      {history.data?.length === 0 && <p>Ask about spending, budgets, or overdue debts.</p>}
      {history.data?.map((item, i) => <article className="living-message" key={i}>
        <strong>{item.prompt}</strong><small>{item.source === "local" ? "Local analysis" : "External provider"}</small>
        <SanitizedMessage text={item.reply} /><small>Personal {vnd(item.metrics.personal)} · Group {vnd(item.metrics.group)} · Overdue {vnd(item.metrics.overdue)}</small>
        {item.quick_actions.map(budget => <button key={budget.category} className="secondary" disabled={action.busy}
          onClick={() => void action.run(async () => {
            await api.put("/budgets", { category: budget.category, monthly_limit: budget.monthly_limit, month: budget.month });
            setNotice(`Saved the ${budget.category.toLowerCase()} budget for ${budget.month.slice(0, 7)}.`);
            reload();
          })}>Apply {budget.category.toLowerCase()} limit · {vnd(budget.monthly_limit)}</button>)}
      </article>)}
    </div>
    <form className="living-card form" onSubmit={submit}><label>Question<textarea name="prompt" maxLength={1500} required /></label>
      <label className="living-check"><input type="checkbox" checked={external} onChange={event => setExternal(event.target.checked)} />
        Share this question and spending totals with the configured AI provider</label>
      <button className="primary" disabled={action.busy}>{action.busy ? "Working…" : "Send question"}</button>
    </form>
  </section>;
}
