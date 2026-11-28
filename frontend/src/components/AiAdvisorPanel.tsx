import { useEffect, useRef, useState, type FormEvent } from "react";
import { groupCategoryLabels } from "./GroupBudgetInsights";
import SanitizedMessage from "./SanitizedMessage";
import { api, streamAdvisor } from "../lib/api";
import { type Advice, type Category, useAction, useResource, vnd } from "../lib/living";

type Item = { amount: number; category: Category; note: string; spent_at: string };
type AgentAction = { id: string; status: "proposed" | "applied" | "undone" } & (
  { tool: "record_personal_expenses"; details: { items: Item[] } } |
  { tool: "upsert_monthly_budget"; details: { category: Category | "OVERALL"; limit_amount: number; month?: string } } |
  { tool: "create_group_expense_draft_or_record"; details: { group_id: number; description: string; amount: number; members: { id: number; name: string; amount: number }[] } }
);
type Budget = { spent: number; limit: number | null; usage_percent: number | null; previous: number; daily: number };
type Turn = Advice & { action?: AgentAction; suggestions?: string[]; visual?: {
  budget?: Budget; debts?: { group_id: number; name: string; transfer_count: number; pending_count: number }[];
} };

const starters = ["Tháng này tôi đã chi bao nhiêu?", "Tháng này nhóm đã chi bao nhiêu?", "Nhóm còn khoản nợ nào?", "Ghi ăn sáng 35k, đổ xăng 50k, mua sách 120k"];

function ActionCard({ value, busy, apply }: {
  value: AgentAction; busy: boolean; apply: (value: AgentAction, operation: "confirm" | "undo") => void;
}) {
  return <section className="living-card living-stack" aria-label="Thao tác tài chính">
    <strong>{value.status === "proposed" ? "Chờ bạn xác nhận" : value.status === "applied" ? "Đã lưu" : "Đã hoàn tác"}</strong>
    {value.tool === "record_personal_expenses" ? <ul>{value.details.items.map((item, i) =>
      <li key={i}>{item.note} · {groupCategoryLabels[item.category]} · {vnd(item.amount)} · {item.spent_at}</li>)}</ul> :
      value.tool === "upsert_monthly_budget" ? <p>Ngân sách {value.details.category === "OVERALL" ? "tổng chi" :
        groupCategoryLabels[value.details.category]} · {vnd(value.details.limit_amount)} · {value.details.month ?? "Tháng này"}</p> :
        <div><p>{value.details.description} · {vnd(value.details.amount)} · Chia đều cho thành viên nhóm #{value.details.group_id}</p>
          <ul>{value.details.members.map(member => <li key={member.id}>{member.name} · {vnd(member.amount)}</li>)}</ul></div>}
    {value.status !== "undone" && <div className="living-buttons">
      <button type="button" disabled={busy} className={value.status === "proposed" ? "primary" : "secondary"}
        onClick={() => apply(value, value.status === "proposed" ? "confirm" : "undo")}>
        {value.status === "proposed" ? "Xác nhận và lưu" : "Hoàn tác"}
      </button>
    </div>}
  </section>;
}

export default function AiAdvisorPanel({ groupId, revision, reload }: {
  groupId?: number; revision: number; reload: () => void;
}) {
  const history = useResource<Turn[]>("/ai-chat/history", revision);
  const action = useAction();
  const inFlight = useRef<AbortController | null>(null);
  const [external, setExternal] = useState(false);
  const [partial, setPartial] = useState("");
  const [notice, setNotice] = useState("");
  const [status, setStatus] = useState("");
  useEffect(() => () => inFlight.current?.abort(), []);

  function send(text: string, onSuccess?: () => void) {
    if (!text.trim() || inFlight.current || action.busy) return;
    const controller = new AbortController();
    inFlight.current = controller;
    void action.run(async () => {
      setPartial("");
      setStatus("Đang kết nối với Trợ lý…");
      const timer = window.setTimeout(() => controller.abort(), 40000);
      try {
        await streamAdvisor({ text: text.trim(), group_id: groupId, use_external: external }, controller.signal,
          (event, data) => {
            if (event === "status") setStatus((data as { text: string }).text);
            if (event === "text") setPartial(previous => previous + (data as { text: string }).text);
            if (event === "error") throw new Error((data as { text: string }).text);
            if (event === "done") {
              history.setData(previous => [...(previous ?? []), data as Turn].slice(-50));
              onSuccess?.();
              reload();
            }
          });
      } finally {
        window.clearTimeout(timer);
        inFlight.current = null;
        setPartial("");
        setStatus("");
      }
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    send(String(new FormData(form).get("prompt") ?? ""), () => form.reset());
  }

  function apply(value: AgentAction, operation: "confirm" | "undo") {
    void action.run(async () => {
      setStatus(operation === "confirm" ? "Đang lưu thay đổi…" : "Đang hoàn tác…");
      try {
        const { data } = await api.post<AgentAction>(`/ai-chat/actions/${value.id}/${operation}`);
        history.setData(previous => previous?.map(turn => turn.action?.id === data.id ? { ...turn, action: data } : turn));
        reload();
      } finally { setStatus(""); }
    });
  }

  return <section className="living-stack"><h1>Trợ lý Tài chính Shared Living</h1>
    <div className="living-buttons">
      {starters.map(text => <button key={text} className="secondary" disabled={action.busy}
        onClick={() => send(text)}>{text}</button>)}
    </div>
    {notice && <p role="status">{notice}</p>}
    {(action.error || history.error) && <p role="alert" className="error">{action.error || history.error}</p>}
    <div className="living-card living-chat">
      {!history.data?.length && <p>{history.loading ? "Đang tải hội thoại…" : "Hỏi về chi tiêu, ngân sách hoặc các khoản nợ."}</p>}
      {history.data?.map((turn, i) => <article className="living-message" key={i}>
        <strong>{turn.prompt}</strong>
        <SanitizedMessage text={turn.reply} />
        {turn.quick_actions?.map(budget => <button key={budget.category} className="secondary" disabled={action.busy}
          onClick={() => void action.run(async () => {
            await api.put("/budgets", { category: budget.category, monthly_limit: budget.monthly_limit, month: budget.month });
            setNotice("Đã lưu ngân sách.");
            reload();
          })}>Áp dụng ngân sách {groupCategoryLabels[budget.category]} · {vnd(budget.monthly_limit)}</button>)}
        {turn.action && <ActionCard value={turn.action} busy={action.busy} apply={apply} />}
        {turn.visual?.budget && <div className="living-card">
          <p>Bình quân {vnd(turn.visual.budget.daily)}/ngày · Tháng trước {vnd(turn.visual.budget.previous)}</p>
          {turn.visual.budget.limit && <label>
            Đã dùng {turn.visual.budget.usage_percent}% ngân sách
            <progress className="advisor-progress" max={turn.visual.budget.limit}
              value={Math.min(turn.visual.budget.spent, turn.visual.budget.limit)} />
          </label>}
        </div>}
        {turn.visual?.debts && <ul className="group-members">{turn.visual.debts.map(group => <li key={group.group_id} className="pill">
          {group.name} · {group.transfer_count} lượt chuyển · {group.pending_count} biên lai chờ duyệt
        </li>)}</ul>}
        {i === (history.data?.length ?? 0) - 1 && turn.suggestions?.length
          ? <div className="living-buttons">{turn.suggestions.map(text => <button className="secondary" key={text}
            disabled={action.busy} onClick={() => send(text)}>{text}</button>)}</div> : null}
      </article>)}
      {status && <p role="status">{status}</p>}
      {partial && <p className="sanitized-message">{partial}</p>}
    </div>
    <form className="living-card form" onSubmit={submit}>
      <label>Câu hỏi hoặc khoản chi<textarea name="prompt" maxLength={1500} required /></label>
      <label className="living-check"><input type="checkbox" checked={external} onChange={event => setExternal(event.target.checked)} />
        Cho phép gửi câu hỏi và ngữ cảnh hội thoại gần đây tới Gemini</label>
      <div className="living-buttons">
        <button className="primary" disabled={action.busy}>Gửi</button>
        <button type="button" className="secondary" disabled={action.busy || !history.data?.length} onClick={() => {
          if (window.confirm("Xóa lịch sử hội thoại? Các khoản chi đã lưu vẫn được giữ.")) {
            void action.run(async () => {
              await api.delete("/ai-chat/history");
              history.setData([]);
              reload();
            });
          }
        }}>Xóa hội thoại</button>
      </div>
    </form>
  </section>;
}
