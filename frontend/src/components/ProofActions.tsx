import { useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { confirm } from "@tauri-apps/plugin-dialog";
import { api, explain } from "../lib/api";

export default function ProofActions({ id, done }: { id: number; done: (approved: boolean) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function decide(approve: boolean) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const message = approve ? "Confirm the funds arrived. Approval marks this debt paid and cannot be reversed here."
        : "Reject this receipt and reopen the debt for a new submission?";
      const accepted = isTauri() ? await confirm(message, { title: "Review payment proof", kind: "warning" }) : window.confirm(message);
      if (!accepted) return;
      await api.post(`/finance/proofs/${id}/decision`, { approve });
      done(approve);
    } catch (e) { setError(explain(e)); } finally { setBusy(false); }
  }
  return <div><div className="actions"><button className="primary" disabled={busy} onClick={() => void decide(true)}>Approve</button>
    <button className="secondary danger" disabled={busy} onClick={() => void decide(false)}>Reject</button></div>
    {error && <p role="alert" className="error">{error}</p>}</div>;
}
