import { useState } from "react";
import { api } from "../lib/api";
import { type Category, type Group, useAction, useResource, vnd } from "../lib/living";

export const groupCategoryLabels: Record<Category, string> = {
  FOOD: "Ăn uống", TRANSPORT: "Đi lại", HOUSING: "Nhà ở", STUDY: "Học tập",
  ENTERTAINMENT: "Giải trí", OTHER: "Khác",
};

export type Insights = {
  period: string; current_month_spend: number; previous_month_spend: number; monthly_limit: number | null;
  usage_percent: number | null; status: "normal" | "warning" | "exceeded"; top_category: Category | null;
  summary: string;
};

export default function GroupBudgetInsights({ group, revision, reload }: {
  group: Group; revision: number; reload: () => void;
}) {
  const path = `/groups/${group.id}`;
  const { data, error, loading } = useResource<Insights>(`${path}/advisor/insights`, revision);
  const action = useAction(reload);
  const [dismissed, setDismissed] = useState("");
  const alert = data?.status === "warning" ? "Sắp chạm ngân sách" : "Đã chạm hoặc vượt ngân sách";
  const alertKey = `${data?.period}:${data?.status}`;
  return <section className="living-card living-stack">
    <details open>
      <summary>Trợ lý AI &amp; Cảnh báo ngân sách</summary>
      {loading && !data && <p role="status">Đang tính chi tiêu nhóm…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {data && <div className="living-stack living-budget">
        <p>{data.summary}</p>
        {data.top_category && <p>Chi nhiều nhất cho: {groupCategoryLabels[data.top_category]}</p>}
        {data.monthly_limit !== null && <label>
          Đã chi {vnd(data.current_month_spend)} / {vnd(data.monthly_limit)} ({data.usage_percent}%)
          <progress max={data.monthly_limit} value={Math.min(data.current_month_spend, data.monthly_limit)} />
        </label>}
        {data.status !== "normal" && dismissed !== alertKey && <div className="living-buttons" role="status">
          <strong className={`pill ${data.status === "warning" ? "wait" : "late"}`}>{alert}</strong>
          <button className="secondary" onClick={() => setDismissed(alertKey)}>Ẩn cảnh báo</button>
        </div>}
      </div>}
      {group.role === "OWNER" && <form key={data?.monthly_limit} className="form" onSubmit={event => {
        event.preventDefault();
        const amount = Number(new FormData(event.currentTarget).get("limit"));
        void action.run(async () => { await api.patch(`${path}/budget-limit`, { monthly_limit: amount }); });
      }}>
        <label>Ngân sách mỗi tháng (đồng)<input name="limit" type="number" inputMode="numeric"
          required min={1} max={10**12} defaultValue={data?.monthly_limit ?? ""} /></label>
        {action.error && <p role="alert" className="error">{action.error}</p>}
        <button className="secondary" disabled={action.busy}>Lưu ngân sách</button>
      </form>}
    </details>
  </section>;
}
