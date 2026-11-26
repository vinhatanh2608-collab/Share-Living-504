import { useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { type ChatItem, type Group, useAction, useResource } from "../lib/living";
import { ProofReview } from "./SmartDebtsAndQrPage";

export default function GroupChatPage({ group, userId, revision, reload }: {
  group: Group; userId: number; revision: number; reload: () => void;
}) {
  const path = `/groups/${group.id}/chat`;
  const messages = useResource<ChatItem[]>(path, revision);
  const [older, setOlder] = useState<ChatItem[]>([]);
  const [more, setMore] = useState(true);
  const [proof, setProof] = useState<{ id: number; creditor: boolean }>();
  const action = useAction();
  const items = [...new Map([...older, ...messages.data ?? []].map(item => [item.id, item])).values()];
  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const value = String(new FormData(form).get("message")).trim();
    if (!value) return;
    void action.run(async () => {
      await api.post(path, { text: value });
      form.reset();
      reload();
    });
  }
  return <section className="living-stack"><h1>{group.name} chat</h1>
    {(messages.error || action.error) && <p role="alert" className="error">{messages.error || action.error}</p>}
    <div className="living-card living-chat">
      {more && (older.length > 0 || messages.data?.length === 50) && <button className="secondary" disabled={action.busy}
        onClick={() => void action.run(async () => {
          const { data } = await api.get<ChatItem[]>(path, { params: { before: items[0].id } });
          setOlder(previous => [...data, ...previous]); setMore(data.length === 50);
        })}>Load older messages</button>}
      {!items.length && <p>{messages.loading ? "Loading messages…" : "No messages yet."}</p>}
      {items.map(item => <article className="living-message" key={item.id}>
        <small>{new Date(item.created_at).toLocaleString()}</small>
        {item.kind === "TEXT" ? <><strong>{item.sender_name}</strong><p>{item.text}</p></> : <>
          <strong>Payment proof #{item.proof_id}</strong><p>{item.status}</p>
          {(item.creditor_id === userId || item.sender_id === userId) && <button className="secondary" onClick={() =>
            setProof({ id: item.proof_id, creditor: item.creditor_id === userId })}>Open proof</button>}
        </>}
      </article>)}
    </div>
    <form className="living-card form" onSubmit={send}><label>Message<textarea name="message" required maxLength={2000} /></label>
      <button className="primary" disabled={action.busy}>Send message</button></form>
    {proof && <ProofReview groupId={group.id} proofId={proof.id} creditor={proof.creditor} close={() => setProof(undefined)} reload={reload} />}
  </section>;
}
