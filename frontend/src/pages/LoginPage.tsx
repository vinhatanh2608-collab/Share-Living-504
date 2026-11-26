import { ArrowRight, House, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useAuthStore } from "../store/useAuthStore";
import { explain } from "../lib/api";

export default function LoginPage() {
  const login = useAuthStore(s => s.login);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try { await login(username.trim(), password); }
    catch (e) { setError(explain(e)); } finally { setBusy(false); }
  }
  return <main className="login-layout">
    <section className="login-hero">
      <div className="brand"><span><House size={21}/></span><div>Dormitory Desk<small>ROOM WORKSPACE</small></div></div>
      <div className="login-hero-copy"><p className="eyebrow">MADE FOR ROOMMATES</p><h1>Room life,<br/>sorted.</h1>
        <p>One workspace for shared bills, room chat, and the notes you actually need.</p></div>
      <div className="login-footer"><ShieldCheck size={18}/> Private workspace · 8 roommates</div>
    </section>
    <section className="login-main"><form className="form" onSubmit={submit}>
      <p className="eyebrow">WELCOME BACK</p><h2>Sign in to your room</h2><p className="muted">Use the account provided by your room owner.</p>
      <label>Username<input autoComplete="username" autoFocus required maxLength={64} value={username} onChange={e => setUsername(e.target.value)} /></label>
      <label>Password<input autoComplete="current-password" required maxLength={72} type="password" value={password} onChange={e => setPassword(e.target.value)} /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}<ArrowRight size={17}/></button>
    </form></section>
  </main>;
}
