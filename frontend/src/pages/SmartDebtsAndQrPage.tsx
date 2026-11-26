import { useEffect, useState, type FormEvent } from "react";
import Modal from "../components/Modal";
import { api, explain, getObject, upload } from "../lib/api";
import { compressReceipt } from "../lib/imageCompress";
import { type Debt, type Group, type Member, useAction, useResource, vnd } from "../lib/living";
import type { Ticket } from "../types/api";

function Directory({ members }: { members: Member[] }) {
  return <div className="living-grid">{members.filter(member => member.qr).map(member => {
    const qr = member.qr!;
    const url = `https://img.vietqr.io/image/${qr.bank_bin}-${qr.bank_account_no}-compact2.png?` +
      new URLSearchParams({ accountName: qr.bank_account_name });
    return <div className="living-card living-qr" key={member.id}><strong>{member.name}</strong>
      <img src={url} alt={`VietQR for ${member.name}`} loading="lazy" /><span>{qr.bank_account_no}</span></div>;
  })}</div>;
}

export function ProofReview({ groupId, proofId, creditor, close, reload }: {
  groupId: number; proofId: number; creditor: boolean; close: () => void; reload: () => void;
}) {
  const path = `/groups/${groupId}/proofs/${proofId}`;
  const receipt = useResource<{ url: string; status: string; reject_reason: string | null }>(path);
  const [image, setImage] = useState("");
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const action = useAction(() => { reload(); close(); });
  const url = receipt.data?.url;
  useEffect(() => {
    if (!url) return;
    let objectUrl = "";
    let active = true;
    void getObject(url).then(async blob => {
      if (blob.size >= 300_000) throw new Error("Receipt exceeds the size limit.");
      const header = new Uint8Array(await blob.slice(0, 3).arrayBuffer());
      if (header[0] !== 255 || header[1] !== 216 || header[2] !== 255) throw new Error("Receipt is not a JPEG image.");
      if (active) {
        objectUrl = URL.createObjectURL(new Blob([blob], { type: "image/jpeg" }));
        setImage(objectUrl);
      }
    }).catch(error => { if (active) setError(explain(error)); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [url]);
  function decide(decision: "APPROVE" | "REJECT") {
    void action.run(() => api.patch(`${path}/verify`, { decision, reject_reason: decision === "REJECT" ? reason : null }));
  }
  return <Modal title={`Receipt #${proofId}`} close={close} busy={action.busy}>
    <div className="form">
      {receipt.data && <p>{receipt.data.status}{receipt.data.reject_reason && ` · ${receipt.data.reject_reason}`}</p>}
      {!image && !receipt.error && !error && <p role="status">Loading receipt…</p>}
      {(receipt.error || error || action.error) && <p className="error" role="alert">{receipt.error || error || action.error}</p>}
      {image && <img className="living-receipt" src={image} alt="Submitted payment receipt" />}
      {creditor && receipt.data?.status === "PENDING" && <>
        <button className="primary" disabled={action.busy || !image} onClick={() => decide("APPROVE")}>Approve payment</button>
        <label>Rejection reason<textarea value={reason} maxLength={300} onChange={event => setReason(event.target.value)} /></label>
        <button className="secondary" disabled={action.busy || !reason.trim()} onClick={() => decide("REJECT")}>Reject payment</button>
      </>}
    </div>
  </Modal>;
}

function Payment({ groupId, ids, members, close, reload }: {
  groupId: number; ids: number[]; members: Member[]; close: () => void; reload: () => void;
}) {
  const path = `/groups/${groupId}`;
  const [tab, setTab] = useState<"pay" | "directory">("pay");
  const [quote, setQuote] = useState<{ amount: number; qr_url: string }>();
  const [quoteError, setQuoteError] = useState("");
  const [uploaded, setUploaded] = useState<string>();
  const action = useAction(() => { reload(); close(); });
  useEffect(() => {
    const controller = new AbortController();
    void api.post<{ amount: number; qr_url: string }>(`${path}/debts/qr`, { debt_ids: ids }, { signal: controller.signal })
      .then(response => { if (!controller.signal.aborted) setQuote(response.data); })
      .catch(error => { if (!controller.signal.aborted) setQuoteError(explain(error)); });
    return () => controller.abort();
  }, [path, ids]);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = new FormData(event.currentTarget).get("receipt");
    void action.run(async () => {
      let uploadId = uploaded;
      if (!uploadId) {
        if (!(file instanceof File) || !file.size) throw new Error("Choose a receipt image.");
        const blob = await compressReceipt(file);
        const { data } = await api.post<Ticket>(`${path}/proofs/upload`, { debt_ids: ids, size: blob.size, content_type: blob.type });
        await upload(data, blob);
        uploadId = data.upload_id;
        setUploaded(uploadId);
      }
      await api.post(`${path}/proofs`, { debt_ids: ids, upload_id: uploadId });
    });
  }
  return <Modal title="Thanh toán · Submit proof" close={close} busy={action.busy}>
    <div className="living-buttons"><button className="secondary" aria-pressed={tab === "pay"} onClick={() => setTab("pay")}>Creditor VietQR</button>
      <button className="secondary" aria-pressed={tab === "directory"} onClick={() => setTab("directory")}>Group QR directory</button></div>
    {tab === "directory" ? <Directory members={members} /> : <div className="form">
      {quote ? <div className="living-qr"><strong>{vnd(quote.amount)}</strong><img src={quote.qr_url} alt="Exact payment VietQR" /></div>
        : <p role={quoteError ? "alert" : "status"}>{quoteError || "Loading payment details…"}</p>}
      <form className="form" onSubmit={submit}>
        <label>Receipt image<input name="receipt" type="file" accept="image/jpeg,image/png,image/webp"
          required={!uploaded} disabled={action.busy} onChange={() => setUploaded(undefined)} /></label>
        {action.error && <p className="error" role="alert">{action.error}</p>}
        <button className="primary" disabled={action.busy || !quote}>{uploaded ? "Retry submission" : "Submit proof"}</button>
      </form>
    </div>}
  </Modal>;
}

export default function SmartDebtsAndQrPage({ group, userId, revision, reload }: {
  group: Group; userId: number; revision: number; reload: () => void;
}) {
  const path = `/groups/${group.id}`;
  const debts = useResource<Debt[]>(`${path}/debts`, revision);
  const directory = useResource<Member[]>(`${path}/members`, revision);
  const [selected, setSelected] = useState<number[]>([]);
  const [payment, setPayment] = useState<number[]>();
  const [proof, setProof] = useState<{ id: number; creditor: boolean }>();
  const [notice, setNotice] = useState("");
  const action = useAction(reload);
  const rows = debts.data ?? [];
  const owed = rows.filter(row => row.debtor_id === userId);
  const receivable = rows.filter(row => row.creditor_id === userId);
  const chosen = owed.filter(row => row.status === "UNPAID" && selected.includes(row.id));
  const names = new Map(directory.data?.map(member => [member.id, member.name]));
  return <section className="living-stack"><h1>Debts and VietQR</h1>
    {[debts.error, directory.error, action.error].filter(Boolean).map((error, index) => <p className="error" role="alert" key={index}>{error}</p>)}
    {notice && <p role="status">{notice}</p>}
    <div className="living-card"><h2>I owe</h2>
      {owed.length === 0 && <p>No debts found.</p>}
      {owed.map(row => <div className="living-row" key={row.id}>
        <span>{row.title} · {names.get(row.creditor_id) ?? `Member #${row.creditor_id}`} · {vnd(row.amount)} · {row.overdue ? "Overdue" : row.status}</span>
        {row.status === "UNPAID" && <label className="living-check"><input type="checkbox" checked={selected.includes(row.id)}
          disabled={chosen.length >= 25 && !selected.includes(row.id) || Boolean(chosen[0] && chosen[0].creditor_id !== row.creditor_id)}
          onChange={event => setSelected(ids => event.target.checked ? [...ids, row.id] : ids.filter(id => id !== row.id))} /> Select</label>}
        {row.proof_id !== null && <button className="secondary" onClick={() => setProof({ id: row.proof_id!, creditor: false })}>View proof</button>}
      </div>)}
      <button className="primary" disabled={!chosen.length} onClick={() => setPayment(chosen.map(row => row.id))}>Thanh toán</button>
    </div>
    <div className="living-card"><h2>Owed to me</h2>
      {receivable.length === 0 && <p>No receivables found.</p>}
      {receivable.map(row => <div className="living-row" key={row.id}>
        <span>{row.title} · {names.get(row.debtor_id) ?? `Member #${row.debtor_id}`} · {vnd(row.amount)} · {row.status}</span>
        {row.status === "UNPAID" && <button className="secondary" disabled={action.busy} onClick={() => void action.run(async () => {
          const { data } = await api.post<{ notified: number }>(`${path}/debts/remind`, { debt_ids: [row.id] });
          setNotice(data.notified ? "Reminder sent." : "No reminder sent. Try again after 24 hours.");
        })}>Gửi nhắc nợ</button>}
        {row.proof_id !== null && <button className="secondary" onClick={() => setProof({ id: row.proof_id!, creditor: true })}>
          {row.status === "PENDING" ? "Review proof" : "View proof"}
        </button>}
      </div>)}
    </div>
    <div className="living-card"><h2>Group QR directory</h2><Directory members={directory.data ?? []} /></div>
    {payment && <Payment groupId={group.id} ids={payment} members={directory.data ?? []} close={() => setPayment(undefined)}
      reload={() => { setSelected([]); reload(); }} />}
    {proof && <ProofReview groupId={group.id} proofId={proof.id} creditor={proof.creditor} close={() => setProof(undefined)} reload={reload} />}
  </section>;
}
