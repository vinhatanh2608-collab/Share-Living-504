import { useState, type FormEvent } from "react";
import { groupCategoryLabels, type Insights } from "../components/GroupBudgetInsights";
import SanitizedMessage from "../components/SanitizedMessage";
import { api } from "../lib/api";
import { type Advice, type Summary, useAction, useResource, vnd } from "../lib/living";

const prompts = [
  ["spend", "Tháng này nhóm đã tiêu hết bao nhiêu?"],
  ["category", "Danh mục nào tốn nhiều tiền nhất?"],
  ["limit", "Nhóm có sắp vượt ngân sách tháng không?"],
] as const;

export default function AiAdvisorPage({ groupId, revision, reload }: {
  groupId?: number; revision: number; reload: () => void;
}) {
  const [quickAnswer, setQuickAnswer] = useState<{ question: string; reply: string }>();
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
  return <section className="living-stack"><h1>Trợ lý tài chính</h1>
    {snapshot && (snapshot.overdue > 0 || snapshot.budgets.some(budget => budget.spent * 5 >= budget.monthly_limit * 4)) &&
      <div className="living-card" role="status">
        {snapshot.overdue > 0 && <p>Tổng nợ quá hạn: {vnd(snapshot.overdue)}.</p>}
        {snapshot.budgets.filter(budget => budget.spent * 5 >= budget.monthly_limit * 4).map(budget =>
          <p key={budget.category}>{groupCategoryLabels[budget.category]} đã dùng {Math.floor(budget.spent * 100 / budget.monthly_limit)}% ngân sách.</p>)}
      </div>}
    {(action.error || history.error || summary.error) && <p role="alert" className="error">{action.error || history.error || summary.error}</p>}
    {notice && <p role="status">{notice}</p>}
    <section className="living-card living-stack guide-examples">
      <h2>Câu hỏi mẫu về nhóm đang chọn</h2>
      {!groupId && <p>Chọn nhóm đã được duyệt để hỏi về chi tiêu của nhóm.</p>}
      <div className="living-buttons">{prompts.map(([topic, question]) => <button key={topic} className="secondary"
        disabled={!groupId || action.busy} onClick={() => {
          if (!groupId) return;
          void action.run(async () => {
            const { data } = await api.get<Insights>(`/groups/${groupId}/advisor/insights`);
            const reply = topic === "spend" ? `Tháng này nhóm đã chi ${vnd(data.current_month_spend)}.` :
              topic === "category" ? (data.top_category ? `Nhóm chi nhiều nhất cho ${groupCategoryLabels[data.top_category]}.` : "Tháng này chưa có khoản chi nào.") :
                data.monthly_limit == null ? "Nhóm chưa đặt ngân sách tháng." :
                  `Nhóm đã dùng ${data.usage_percent}% ngân sách. ${data.status === "exceeded" ? "Đã chạm hoặc vượt hạn mức." :
                    data.status === "warning" ? "Nhóm sắp chạm hạn mức." : "Chi tiêu vẫn trong hạn mức."}`;
            setQuickAnswer({ question, reply });
          });
        }}>{question}</button>)}</div>
      {quickAnswer && <div className="living-message" role="status">
        <strong>{quickAnswer.question}</strong><p>{quickAnswer.reply}</p>
      </div>}
    </section>
    <div className="living-card living-chat">
      {history.data?.length === 0 && <p>Hãy hỏi về chi tiêu, ngân sách hoặc nợ quá hạn.</p>}
      {history.data?.map((item, i) => <article className="living-message" key={i}>
        <strong>{item.prompt}</strong><small>{item.source === "local" ? "Phân tích tại chỗ" : "Dịch vụ AI bên ngoài"}</small>
        <SanitizedMessage text={item.reply} /><small>Cá nhân {vnd(item.metrics.personal)} · Nhóm {vnd(item.metrics.group)} · Quá hạn {vnd(item.metrics.overdue)}</small>
        {item.quick_actions.map(budget => <button key={budget.category} className="secondary" disabled={action.busy}
          onClick={() => void action.run(async () => {
            await api.put("/budgets", { category: budget.category, monthly_limit: budget.monthly_limit, month: budget.month });
            setNotice(`Đã lưu ngân sách ${groupCategoryLabels[budget.category]} cho tháng ${budget.month.slice(0, 7)}.`);
            reload();
          })}>Áp dụng ngân sách {groupCategoryLabels[budget.category]} · {vnd(budget.monthly_limit)}</button>)}
      </article>)}
    </div>
    <form className="living-card form" onSubmit={submit}><label>Câu hỏi<textarea name="prompt" maxLength={1500} required /></label>
      <label className="living-check"><input type="checkbox" checked={external} onChange={event => setExternal(event.target.checked)} />
        Chia sẻ câu hỏi và tổng chi tiêu với dịch vụ AI đã cấu hình</label>
      <button className="primary" disabled={action.busy}>{action.busy ? "Đang xử lý…" : "Gửi câu hỏi"}</button>
    </form>
  </section>;
}
