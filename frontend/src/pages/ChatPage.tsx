import { ChevronUp, MessageCircle, Send, ShieldCheck } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { api, explain } from "../lib/api";
import { initials } from "../lib/format";
import { useRoomStore } from "../store/useRoomStore";
import type { ProofState, TextMessage, User } from "../types/api";
import ProofActions from "../components/ProofActions";
import ImageLightbox from "../components/ImageLightbox";

export default function ChatPage({ user }: { user: User }) {
  const { roommates, messages, cursor, history, ingest, chatActive, refresh } = useRoomStore();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [receipt, setReceipt] = useState<number>();
  const [decisions, setDecisions] = useState<Record<number, ProofState>>({});
  const feed = useRef<HTMLDivElement>(null);
  const preserve = useRef<{ height: number; top: number }>();
  useEffect(() => {
    chatActive(true);
    void history().catch(e => setError(explain(e)));
    return () => chatActive(false);
  }, [chatActive, history]);
  useLayoutEffect(() => {
    if (!feed.current) return;
    if (preserve.current) {
      feed.current.scrollTop = preserve.current.top + feed.current.scrollHeight - preserve.current.height;
      preserve.current = undefined;
    } else feed.current.scrollTop = feed.current.scrollHeight;
  }, [messages]);
  async function load() {
    if (loading) return;
    setLoading(true); setError("");
    if (feed.current) preserve.current = { height: feed.current.scrollHeight, top: feed.current.scrollTop };
    try { await history(true); }
    catch (e) { preserve.current = undefined; setError(explain(e)); } finally { setLoading(false); }
  }
  async function send(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true); setError("");
    try { const { data } = await api.post<TextMessage>("/chat/messages", { text }); ingest(data); setDraft(""); }
    catch (e) { setError(explain(e)); } finally { setSending(false); }
  }
  return <div className="page chat-page"><header className="page-header"><div><p className="eyebrow">ROOM CONVERSATION</p><h1>Room chat</h1><p>A private space for everyday room updates.</p></div><span className="pill"><MessageCircle size={15}/> {roommates.length || 8} roommates</span></header>
    <section className="panel chat"><div className="feed" ref={feed} aria-label="Messages">
      {cursor && <button className="secondary older" onClick={() => void load()} disabled={loading}><ChevronUp size={16}/> {loading ? "Loading…" : "Load older messages"}</button>}
      {!messages.length && <p className="empty">No messages yet. Start the conversation.</p>}
      {messages.map(m => {
        if (m.kind === "payment_card") {
          const state = m.card.status === "pending" ? decisions[m.card.proof_id] ?? "pending" : m.card.status;
          const canReview = m.card.creditor_id === user.id;
          const canView = canReview || m.card.debtor_id === user.id;
          return <article key={m.id} className="payment-card"><div className="section-head"><div><h2><ShieldCheck size={18}/> Payment proof</h2><p>Debt #{m.card.debt_id} · Proof #{m.card.proof_id}</p></div><span className={`pill ${state === "pending" ? "wait" : state === "rejected" ? "late" : ""}`}>{state}</span></div>
            <div className="actions">{canView && <button className="secondary" onClick={() => setReceipt(m.card.proof_id)}>View receipt</button>}
              {canReview && state === "pending" && <ProofActions id={m.card.proof_id} done={ok => { setDecisions(s => ({ ...s, [m.card.proof_id]: ok ? "paid" : "rejected" })); void refresh(); }}/>}</div></article>;
        }
        const name = roommates.find(r => r.id === m.sender_id)?.name ?? `Roommate #${m.sender_id}`;
        return <article key={m.id} className={`message ${m.sender_id === user.id ? "mine" : ""}`}><span className="avatar">{initials(name)}</span><div><small>{m.sender_id === user.id ? "You" : name} · {new Date(m.created_at.endsWith("Z") || /[+-]\d\d:\d\d$/.test(m.created_at) ? m.created_at : m.created_at + "Z").toLocaleString()}</small><p>{m.text}</p></div></article>;
      })}
    </div>
    {error && <p className="error chat-error" role="alert">{error}</p>}
    <form className="composer" onSubmit={send}><input aria-label="Message" value={draft} disabled={sending} onChange={e => setDraft(e.target.value)} maxLength={4000} placeholder="Write a message…"/><button className="primary" disabled={!draft.trim() || sending} aria-label="Send message"><Send size={17}/></button></form>
    </section>{receipt !== undefined && <ImageLightbox id={receipt} close={() => setReceipt(undefined)}/>}
  </div>;
}
