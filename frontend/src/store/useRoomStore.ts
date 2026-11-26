import { create } from "zustand";
import type { Debt, Message, Page, PaymentMessage, PendingProof, Roommate } from "../types/api";
import { api, explain } from "../lib/api";

async function all<T extends {id: number}>(path: string, signal?: AbortSignal) {
  let after = 0;
  const items: T[] = [];
  while (true) {
    const { data } = await api.get<T[]>(path, { params: { after, limit: 100 }, signal });
    items.push(...data);
    if (data.length < 100) return items;
    const next = data[data.length - 1].id;
    if (next <= after) throw new Error("Invalid API pagination cursor.");
    after = next;
  }
}
export function mergeMessages(current: Message[], incoming: Message[]) {
  const byId = new Map([...current, ...incoming].map(m => [m.id, m]));
  const latest = new Map<number, PaymentMessage>();
  for (const m of byId.values()) if (m.kind === "payment_card") {
    const previous = latest.get(m.card.proof_id);
    if (!previous || previous.event_id < m.event_id) latest.set(m.card.proof_id, m);
  }
  return [...byId.values()].filter(m => m.kind !== "payment_card" || latest.get(m.card.proof_id)?.id === m.id)
    .sort((a,b) => Date.parse(a.created_at) - Date.parse(b.created_at) || a.id.localeCompare(b.id));
}
type Room = {
  roommates: Roommate[]; debts: Debt[]; proofs: PendingProof[]; messages: Message[];
  cursor: string | null; unread: number; chatOpen: boolean; error: string; loading: boolean;
  refresh(owner: boolean): Promise<void>; history(older?: boolean): Promise<void>;
  ingest(message: Message): void; chatActive(value: boolean): void; reset(): void;
};
const empty = { roommates: [], debts: [], proofs: [], messages: [], cursor: null, unread: 0, error: "", loading: false, chatOpen: false };
let lifecycle = 0;
let latestRefresh = 0;
let liveAbort: AbortController | undefined;
let historyBusy = false;
export const useRoomStore = create<Room>((set, get) => ({
  ...empty,
  async refresh(owner) {
    const epoch = lifecycle;
    const refresh = ++latestRefresh;
    liveAbort?.abort();
    liveAbort = new AbortController();
    const { signal } = liveAbort;
    set({ error: "", loading: true });
    try {
      const [directory, debts, proofs] = await Promise.all([
        api.get<Roommate[]>("/auth/roommates", { signal }),
        all<Debt>("/finance/debts", signal), owner ? all<PendingProof>("/finance/proofs/pending", signal) : [],
      ]);
      if (epoch === lifecycle && refresh === latestRefresh) set({ roommates: directory.data, debts, proofs, loading: false });
    } catch (e) {
      if (epoch === lifecycle && refresh === latestRefresh) set({ error: explain(e), loading: false });
    }
  },
  async history(older = false) {
    const current = get();
    if (historyBusy || (older && !current.cursor)) return;
    historyBusy = true;
    const epoch = lifecycle;
    try {
      const { data } = await api.get<Page<Message>>("/chat/messages", {
        params: older ? { before: current.cursor } : {},
      });
      if (epoch === lifecycle) set(state => ({
        messages: mergeMessages(state.messages, data.items),
        cursor: older || !current.messages.length || current.cursor === null ? data.next_cursor : current.cursor,
      }));
    } finally { if (epoch === lifecycle) historyBusy = false; }
  },
  ingest(message) {
    set(s => ({ messages: mergeMessages(s.messages, [message]),
      unread: s.chatOpen || s.messages.some(m => m.id === message.id) ? s.unread : s.unread + 1 }));
  },
  chatActive(chatOpen) { set(s => ({ chatOpen, unread: chatOpen ? 0 : s.unread })); },
  reset() { lifecycle++; liveAbort?.abort(); historyBusy = false; set(empty); },
}));
