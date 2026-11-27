import { useEffect, useState } from "react";
import { api, explain } from "./api";

export const categories = ["FOOD", "TRANSPORT", "HOUSING", "STUDY", "ENTERTAINMENT", "OTHER"] as const;
export type Category = typeof categories[number];
export type Group = { id: number; name: string; code: string | null; role: "OWNER" | "MEMBER";
  status: "PENDING" | "APPROVED" | "REJECTED"; qr_ready: boolean; auto_monthly_reminder: boolean;
  reminder_hour: number; last_reminded_period: string | null };
export type Bank = { bank_bin: string; bank_account_no: string; bank_account_name: string };
export type Member = { id: number; name: string; status: Group["status"]; qr: Bank | null };
export type Debt = { id: number; title: string; amount: number; status: "UNPAID" | "PENDING" | "PAID";
  debtor_id: number; creditor_id: number; proof_id: number | null; overdue: boolean };
export type ChatItem = { id: string; created_at: string; sender_id: number } & (
  { kind: "TEXT"; sender_name: string; text: string } |
  { kind: "DEBT_REMINDER"; sender_name: string; debtor_ids: number[] } |
  { kind: "MONTHLY_REMINDER"; title: string; total_amount: number; transfers: {
    debtor_id: number; debtor_name: string; creditor_id: number; creditor_name: string; amount: number }[] } |
  { kind: "PAYMENT_PROOF"; proof_id: number; creditor_id: number; status: string }
);
export type Budget = { category: Category; monthly_limit: number; spent: number };
export type BudgetAction = Omit<Budget, "spent"> & { type: "apply_budget"; month: string };
export type Summary = { personal: number; group: number; income: number; owed: number; owing: number; overdue: number;
  month: string; budgets: Budget[]; categories: { category: Category; amount: number; percentage: number }[];
  trend: { date: string; spending: number; budget: number }[] };
export type Advice = { prompt: string; reply: string; source: "local" | "provider"; quick_actions: BudgetAction[];
  metrics: { personal: number; group: number; overdue: number } };

export const vnd = (amount: number) => new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND" }).format(amount);
const dateFormat = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit",
});
export function localDate(date = new Date()) {
  const parts = Object.fromEntries(dateFormat.formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

const eventNotices: ReadonlyMap<string, string> = new Map([
  ["group_join_requested", "Có yêu cầu tham gia nhóm mới"], ["group_membership_updated", "Đã cập nhật thành viên nhóm"],
  ["group_debt_reminder", "Bạn nhận được lời nhắc thanh toán"], ["group_proof_submitted", "Có biên lai mới chờ duyệt"],
  ["PROOF_VERIFIED", "Chủ nợ đã xử lý biên lai của bạn"],
]);
export function realtimeUpdate(event: unknown) {
  if (typeof event !== "object" || event === null || !("type" in event) || typeof event.type !== "string") return;
  const notice = eventNotices.get(event.type);
  if (notice || ["group_chat", "connected", "resync"].includes(event.type)) return { notice };
}

export function useResource<T>(path: string | null, revision = 0) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(path));
  useEffect(() => {
    if (!path) { setData(undefined); setError(""); setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api.get<T>(path, { signal: controller.signal }).then(response => {
      if (!controller.signal.aborted) setData(response.data);
    }).catch(error => {
      if (!controller.signal.aborted) setError(explain(error));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [path, revision]);
  return { data, error, loading, setData };
}

export function useAction(onSuccess?: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(action: () => Promise<unknown>) {
    if (busy) return false;
    setBusy(true);
    setError("");
    try { await action(); onSuccess?.(); return true; }
    catch (error) { setError(explain(error)); return false; }
    finally { setBusy(false); }
  }
  return { busy, error, run };
}
