import { CreditCard, QrCode } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useRoomStore } from "../store/useRoomStore";
import { api, explain } from "../lib/api";
import { initials } from "../lib/format";
import Modal from "../components/Modal";
import type { Bank, Roommate, User } from "../types/api";

const blank = { bank_bin: "", account_number: "", account_name: "" };
export default function QrDirectoryPage({ user }: { user: User }) {
  const { roommates, refresh } = useRoomStore();
  const [person, setPerson] = useState<Roommate>();
  const [amount, setAmount] = useState("");
  const [qr, setQr] = useState("");
  const [bank, setBank] = useState<Bank>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  function generate(e: FormEvent) {
    e.preventDefault();
    const details = person?.vietqr;
    if (!details || !/^\d{6}$/.test(details.bank_bin) || !/^\d{4,32}$/.test(details.account_number)) return;
    const n = Number(amount);
    if (!Number.isSafeInteger(n) || n <= 0 || n > 1e12) return;
    const q = new URLSearchParams({ amount: String(n), accountName: details.account_name });
    setQr(`https://img.vietqr.io/image/${details.bank_bin}-${details.account_number}-compact2.png?${q}`);
  }
  async function save(e: FormEvent) {
    e.preventDefault(); setError(""); setBusy(true);
    try { await api.put("/auth/me/vietqr", bank); setBank(undefined); void refresh(); }
    catch (e) { setError(explain(e)); } finally { setBusy(false); }
  }
  return <div className="page"><header className="page-header"><div><p className="eyebrow">PAYMENTS DIRECTORY</p><h1>VietQR directory</h1>
    <p>Generate a payment QR with a custom VND amount.</p></div>
    <button className="secondary" onClick={() => { setError(""); setBank(roommates.find(r => r.id === user.id)?.vietqr ?? blank); }}><CreditCard size={17}/> Edit my bank info</button></header>
    <div className="directory-grid">{roommates.map(r => <article className="panel room-card" key={r.id}>
      <div className="room-profile"><span className="avatar">{initials(r.name)}</span><div><h2>{r.name}</h2><p>{r.id === user.id ? "Your account" : "Roommate"}</p></div></div>
      <p className="muted">{r.vietqr ? `Bank account ending in ${r.vietqr.account_number.slice(-4)}` : "No bank details configured"}</p>
      <button className="secondary" disabled={!r.vietqr} onClick={() => { setPerson(r); setAmount(""); setQr(""); }}><QrCode size={17}/> Generate VietQR</button>
    </article>)}</div>
    {!roommates.length && <p className="panel empty">No directory entries loaded.</p>}
    {person && <Modal title={`VietQR · ${person.name}`} close={() => setPerson(undefined)}><form className="form" onSubmit={generate}>
      <p className="muted">Verify the recipient and amount in your banking app before sending.</p>
      <label>Amount (VND)<input type="number" min="1" max="1000000000000" step="1" required value={amount} onChange={e => { setAmount(e.target.value); setQr(""); }}/></label>
      <button className="primary">Generate QR</button>{qr && <img className="directory-qr" src={qr} referrerPolicy="no-referrer" alt={`VietQR for ${person.name}`} width="260" height="260"/>}
    </form></Modal>}
    {bank && <Modal title="Edit my bank info" close={() => setBank(undefined)} busy={busy}><form className="form" onSubmit={save}>
      <p className="muted">This information is visible to the other roommates.</p>
      <label>Bank BIN (6 digits)<input required pattern="[0-9]{6}" inputMode="numeric" value={bank.bank_bin} onChange={e => setBank({ ...bank, bank_bin: e.target.value })}/></label>
      <label>Account number<input required pattern="[0-9]{4,32}" inputMode="numeric" value={bank.account_number} onChange={e => setBank({ ...bank, account_number: e.target.value })}/></label>
      <label>Account holder<input required maxLength={100} value={bank.account_name} onChange={e => setBank({ ...bank, account_name: e.target.value })}/></label>
      {error && <p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy}>{busy ? "Saving…" : "Save bank info"}</button>
    </form></Modal>}
  </div>;
}
