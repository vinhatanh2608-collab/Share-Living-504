import { create } from "zustand";
import { realtimeUrl } from "../lib/api";
import { notify } from "../lib/notify";
import type { Event, User } from "../types/api";
import { useRoomStore } from "./useRoomStore";

type Connection = "offline" | "connecting" | "online";
export const useRealtimeStore = create<{ status: Connection; notes: number }>(() => ({ status: "offline", notes: 0 }));
export function connectRealtime(token: string, user: User, receive?: (event: unknown) => void) {
  let socket: WebSocket | undefined;
  let retry: ReturnType<typeof setTimeout>;
  let watchdog: ReturnType<typeof setTimeout>;
  let stopped = false;
  let attempts = 0;
  const seen = new Set<number>();
  const refresh = () => {
    const room = useRoomStore.getState();
    void room.refresh(user.is_owner);
    void room.history().catch(() => {});
  };
  function connect() {
    if (stopped || !navigator.onLine) return;
    let connection: WebSocket;
    try {
      const url = new URL(realtimeUrl());
      if (url.username || url.password || !(url.protocol === "wss:" ||
        (url.protocol === "ws:" && ["localhost", "127.0.0.1"].includes(url.hostname)))) throw new Error("Unsafe WS URL");
      url.searchParams.set("token", token);
      useRealtimeStore.setState({ status: "connecting" });
      socket = connection = new WebSocket(url);
    } catch { useRealtimeStore.setState({ status: "offline" }); return; }
    const watch = (timeout: number) => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => connection.close(), timeout);
    };
    watch(12000);
    connection.onopen = () => {
      if (stopped) return;
      watch(45000);
    };
    connection.onmessage = message => {
      if (stopped || typeof message.data !== "string") return;
      try {
        const event = JSON.parse(message.data) as Event;
        watch(45000);
        if (receive) {
          if (event.type === "connected") useRealtimeStore.setState({ status: "online" });
          if (event.type === "heartbeat") attempts = 0;
          else receive(event);
          return;
        }
        const room = useRoomStore.getState();
        switch (event.type) {
          case "connected":
            useRealtimeStore.setState({ status: "online", notes: useRealtimeStore.getState().notes + 1 });
            refresh();
            break;
          case "heartbeat": attempts = 0; break;
          case "resync": refresh(); break;
          case "chat_message": room.ingest(event.message); break;
          case "payment_card":
            if (seen.has(event.event_id)) break;
            seen.add(event.event_id);
            if (seen.size > 500) seen.delete(seen.values().next().value!);
            room.ingest({ kind: "payment_card", id: `proof-event:${event.event_id}`, event_id: event.event_id,
              card: event.card, created_at: new Date().toISOString() });
            void room.refresh(user.is_owner);
            if (event.card.status !== "pending" && event.card.debtor_id === user.id)
              void notify("Payment proof reviewed", `Debt #${event.card.debt_id}: ${event.card.status === "paid" ? "approved" : "rejected"}.`);
            break;
          case "overdue_reminder":
            void notify("Overdue debt reminder", `Open Dormitory Desk to review debt #${event.debt_id}.`);
            void room.refresh(user.is_owner);
            break;
          case "note_updated": useRealtimeStore.setState(s => ({ notes: s.notes + 1 })); break;
        }
      } catch { /* Ignore malformed events; REST remains authoritative. */ }
    };
    connection.onerror = () => connection.close();
    connection.onclose = event => {
      clearTimeout(watchdog);
      socket = undefined;
      if (stopped) return;
      useRealtimeStore.setState({ status: "offline" });
      if (event.code === 1008 || !navigator.onLine) return;
      retry = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(attempts++, 5)) + Math.random() * 400);
    };
  }
  function reconnect() {
    clearTimeout(retry);
    if (!socket) connect();
  }
  function disconnect() {
    clearTimeout(retry);
    socket?.close();
    useRealtimeStore.setState({ status: "offline" });
  }
  window.addEventListener("online", reconnect);
  window.addEventListener("offline", disconnect);
  connect();
  return () => {
    stopped = true;
    window.removeEventListener("online", reconnect);
    window.removeEventListener("offline", disconnect);
    clearTimeout(retry);
    clearTimeout(watchdog);
    if (socket) { socket.onclose = null; socket.onerror = null; socket.close(); }
    useRealtimeStore.setState({ status: "offline", notes: 0 });
  };
}
