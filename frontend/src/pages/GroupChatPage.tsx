import { useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { type ChatItem, type Group, useAction, useResource, vnd } from "../lib/living";
import { ProofReview } from "./SmartDebtsAndQrPage";
import SanitizedMessage from "../components/SanitizedMessage";

export default function GroupChatPage({ group, userId, revision, reload }: {
  group: Group; userId: number; revision: number; reload: () => void;
}) {
  const path = `/groups/${group.id}/chat`;
  const messages = useResource<ChatItem[]>(path, revision);
  const [older, setOlder] = useState<ChatItem[]>([]);
  const [more, setMore] = useState(true);
  const [proof, setProof] = useState<{ id: number; creditor: boolean }>();
  const action = useAction();
  const items = [...new Map([...older, ...(messages.data ?? [])].map(item => [item.id, item])).values()]
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  const cursor = items[0]?.id;
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
  return <section className="living-stack"><h1>Trò chuyện nhóm {group.name}</h1>
    {(messages.error || action.error) && <p role="alert" className="error">{messages.error || action.error}</p>}
    <div className="living-card living-chat">
      {more && cursor && (older.length > 0 || messages.data?.length === 50) && <button className="secondary" disabled={action.busy}
        onClick={() => void action.run(async () => {
          const { data } = await api.get<ChatItem[] | null>(path, { params: { before: cursor } });
          const page = data ?? [];
          setOlder(previous => [...page, ...previous]); setMore(page.length === 50);
        })}>Tải tin nhắn cũ hơn</button>}
      {!items.length && <p>{messages.loading ? "Đang tải tin nhắn…" : "Chưa có tin nhắn."}</p>}
      {items.map(item => <article className="living-message" key={item.id}>
        <small>{new Date(item.created_at).toLocaleString("vi-VN")}</small>
        {item.kind === "TEXT" ? <><strong>{item.sender_name}</strong><SanitizedMessage text={item.text} /></> :
          item.kind === "MONTHLY_REMINDER" ? <>
            <strong>{item.title ?? "Tổng kết và nhắc nợ cuối tháng"}</strong>
            <p>{item.transfers?.length ?? 0} lần chuyển khoản · Tổng {vnd(item.total_amount ?? item.transfers?.reduce((sum, edge) => sum + edge.amount, 0) ?? 0)}</p>
            <ul>{item.transfers?.map(edge => <li key={`${edge.debtor_id}-${edge.creditor_id}`}>
              {edge.debtor_name ?? `Thành viên #${edge.debtor_id}`} chuyển cho {edge.creditor_name ?? `thành viên #${edge.creditor_id}`} · {vnd(edge.amount)}
            </li>)}</ul>
          </> : item.kind === "DEBT_REMINDER" ? <>
            <strong>Nhắc thanh toán</strong>
            <p>{item.sender_name} đã nhắc {item.debtor_ids?.includes(userId) ? "bạn" : "thành viên trong nhóm"} thanh toán khoản còn nợ.</p>
          </> : item.kind === "PAYMENT_PROOF" ? <>
          <strong>Biên lai #{item.proof_id}</strong><p>{item.status === "PAID" ? "Đã xác nhận" : item.status === "REJECTED" ? "Đã từ chối" : "Chờ chủ nợ duyệt"}</p>
          {(item.creditor_id === userId || item.sender_id === userId) && <button className="secondary" onClick={() =>
            setProof({ id: item.proof_id, creditor: item.creditor_id === userId })}>Xem biên lai</button>}
        </> : <p>Định dạng tin nhắn chưa được hỗ trợ.</p>}
      </article>)}
    </div>
    <form className="living-card form" onSubmit={send}><label>Tin nhắn<textarea name="message" required maxLength={2000} /></label>
      <button className="primary" disabled={action.busy}>Gửi tin nhắn</button></form>
    {proof && <ProofReview groupId={group.id} proofId={proof.id} creditor={proof.creditor} close={() => setProof(undefined)} reload={reload} />}
  </section>;
}
