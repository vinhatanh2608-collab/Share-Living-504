import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Debt, Ticket } from "../types/api";
import { api, explain, upload } from "../lib/api";
import { money } from "../lib/format";
import { compressReceipt } from "../lib/imageCompress";
import { vietQrUrl } from "../lib/safeUrl";
import Modal from "./Modal";

export default function SubmitProofModal({ debts, close, done }: { debts: Debt[]; close: () => void; done: () => void }) {
  const [qr, setQr] = useState<Record<number, string>>({});
  const [image, setImage] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const uploaded = useRef<string>();
  useEffect(() => {
    const cont = new AbortController();
    for (const debt of debts) void api.get<{url: string}>(`/finance/debts/${debt.id}/vietqr`, { signal: cont.signal })
      .then(r => {
        const url = vietQrUrl(r.data.url);
        if (!url) throw new Error("Unexpected QR provider");
        if (!cont.signal.aborted) setQr(all => ({ ...all, [debt.id]: url }));
      }).catch(() => { if (!cont.signal.aborted) setQr(all => ({ ...all, [debt.id]: "" })); });
    return () => cont.abort();
  }, [debts]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!image || busy) return;
    setBusy(true); setError("");
    const debt_ids = debts.map(d => d.id);
    try {
      if (!uploaded.current) {
        const blob = await compressReceipt(image);
        const { data } = await api.post<Ticket>("/finance/proof-batches/upload", {
          debt_ids, size: blob.size, content_type: blob.type,
        });
        await upload(data, blob);
        uploaded.current = data.upload_id;
      }
      await api.post("/finance/proof-batches", { debt_ids, upload_id: uploaded.current });
      done(); close();
    } catch (e) { setError(explain(e)); } finally { setBusy(false); }
  }
  return <Modal title="Submit payment proof" close={close} busy={busy}>
    <form className="form" onSubmit={submit}>
      <p>Total selected: <strong>{money(debts.reduce((total, d) => total + d.amount, 0))}</strong></p>
      <p className="muted">Pay outside the app. If submitting for multiple debts, choose one image that documents every selected payment. Uploading does not settle a debt.</p>
      <div className="qr-strip">{debts.map(d => <details key={d.id}>
        <summary>{d.title || `Debt #${d.id}`} · {money(d.amount)} · VietQR</summary>
        {qr[d.id] ? <img src={qr[d.id]} alt={`VietQR for debt ${d.id}`} referrerPolicy="no-referrer" width="190" height="190"/>
          : <p className="muted">{qr[d.id] === "" ? "VietQR unavailable for this debt." : "Loading VietQR…"}</p>}
      </details>)}</div>
      <label>Receipt image<input type="file" accept="image/jpeg,image/png,image/webp" required disabled={busy}
        onChange={e => { uploaded.current = undefined; setImage(e.target.files?.[0]); }}/></label>
      {error && <p role="alert" className="error">{error}</p>}
      <button className="primary" disabled={!image || busy}>{busy ? "Uploading and submitting…" : "Send for review"}</button>
    </form>
  </Modal>;
}
