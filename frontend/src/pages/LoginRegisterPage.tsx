import { useState, type FormEvent } from "react";
import { Building2 } from "lucide-react";
import { api } from "../lib/api";
import { useAction } from "../lib/living";
import { useAuthStore } from "../store/useAuthStore";

export default function LoginRegisterPage() {
  const [register, setRegister] = useState(false);
  const login = useAuthStore(state => state.login);
  const action = useAction();
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const username = String(form.get("username")).trim();
    const password = String(form.get("password"));
    void action.run(async () => {
      if (register) await api.post("/auth/register", { username, password, full_name: form.get("full_name") });
      await login(username, password);
    });
  }
  return <main className="living-auth">
    <section className="living-auth-intro">
      <Building2 size={38} />
      <h1>Shared Living</h1>
      <p>Track what you spend, split household bills, and settle up with your roommates.</p>
    </section>
    <section className="living-auth-panel">
      <form className="form" onSubmit={submit}>
        <h2>{register ? "Create an account" : "Sign in"}</h2>
        {register && <label>Full name<input name="full_name" autoComplete="name" required maxLength={100} /></label>}
        <label>Username<input name="username" autoComplete="username" required maxLength={64}
          pattern={register ? "[A-Za-z0-9_.\\-]{3,64}" : undefined} /></label>
        <label>Password<input name="password" type="password" autoComplete={register ? "new-password" : "current-password"}
          required minLength={register ? 12 : 1} maxLength={72} /></label>
        {action.error && <p role="alert" className="error">{action.error}</p>}
        <button className="primary" disabled={action.busy}>{register ? "Create account" : "Sign in"}</button>
        <button className="secondary" type="button" disabled={action.busy} onClick={() => setRegister(value => !value)}>
          {register ? "Use an existing account" : "Create an account"}
        </button>
      </form>
    </section>
  </main>;
}
