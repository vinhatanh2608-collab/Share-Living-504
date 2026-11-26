export type Bank = { bank_bin: string; account_number: string; account_name: string };
export type Roommate = { id: number; name: string; username: string; vietqr: Bank | null };
export type User = Omit<Roommate, "vietqr"> & { is_owner: boolean };
export type Debt = { id: number; expense_id: number; debtor_id: number; creditor_id: number; amount: number; status: "open" | "pending" | "paid"; due_at: string; title?: string };
export type Ticket = { upload_id: string; url: string; expires_in: number; headers: Record<string, string> };
export type PendingProof = { id: number; debt_id: number; submitted_at: string };
export type ProofState = "pending" | "paid" | "rejected";
export type Card = { proof_id: number; debt_id: number; debtor_id: number; creditor_id: number; status: ProofState };
export type TextMessage = { kind: "text"; id: string; text: string; sender_id: number; created_at: string };
export type PaymentMessage = { kind: "payment_card"; id: string; card: Card; event_id: number; created_at: string };
export type Message = TextMessage | PaymentMessage;
export type Page<T> = { items: T[]; next_cursor: string | null };
export type Material = { id: string; title: string; description: string; owner_id: number; created_at: string };
export type Note = { id: string; text: string; version: number };
export type Event =
  | { type: "connected" | "resync" | "heartbeat" }
  | { type: "chat_message"; message: TextMessage }
  | { type: "payment_card"; event_id: number; card: Card }
  | { type: "note_updated"; id: string; version: number }
  | { type: "overdue_reminder"; debt_id: number };
