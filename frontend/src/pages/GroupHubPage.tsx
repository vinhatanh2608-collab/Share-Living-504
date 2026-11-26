import type { FormEvent } from "react";
import { api } from "../lib/api";
import { type Bank, type Group, type Member, useAction, useResource } from "../lib/living";

export default function GroupHubPage({ groups, active, select, revision, reload, userId }: {
  groups: Group[]; active: Group | undefined; select: (groupId: number) => void;
  revision: number; reload: () => void; userId: number;
}) {
  const action = useAction(reload);
  const directory = useResource<Member[]>(active ? `/groups/${active.id}/members` : null, revision);
  const bank = useResource<Bank | null>("/users/me/bank-info", revision);
  const currentBank = directory.data?.find(member => member.id === userId)?.qr ?? bank.data;
  function groupForm(event: FormEvent<HTMLFormElement>, join: boolean) {
    event.preventDefault();
    const form = event.currentTarget;
    const value = String(new FormData(form).get("group"));
    void action.run(async () => {
      const { data } = await api.post<Group>(join ? "/groups/join" : "/groups", join ? { group_code: value } : { name: value });
      if (data.status === "APPROVED") select(data.id);
      form.reset();
    });
  }
  function saveBank(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    void action.run(async () => {
      await api.put("/users/me/bank-info", values);
      if (active) await api.put(`/groups/${active.id}/members/me/qr`, values);
    });
  }
  return <section className="living-stack">
    <h1>Groups</h1>
    {action.error && <p className="error" role="alert">{action.error}</p>}
    <div className="living-grid">
      <form className="living-card form" onSubmit={event => groupForm(event, false)}>
        <h2>Create a group</h2><label>Group name<input name="group" maxLength={100} required /></label>
        <button className="primary" disabled={action.busy}>Create group</button>
      </form>
      <form className="living-card form" onSubmit={event => groupForm(event, true)}>
        <h2>Request to join</h2><label>Invite code<input name="group" placeholder="GRP-DEMO01" maxLength={16} required /></label>
        <button className="primary" disabled={action.busy}>Send request</button>
      </form>
    </div>
    <div className="living-card">
      <h2>Your groups</h2>
      {groups.length === 0 && <p>Create a group or enter an invite code to get started.</p>}
      {groups.map(group => <div className="living-row" key={group.id}>
        <span>{group.name} · {group.status === "APPROVED" ? group.role.toLowerCase() : group.status.toLowerCase()}</span>
        {group.status === "APPROVED" && <div className="living-buttons">
          <button className="secondary" onClick={() => select(group.id)}>{active?.id === group.id ? "Selected" : "Select"}</button>
          {group.code && <button className="secondary" onClick={() => void action.run(() =>
            navigator.clipboard.writeText(group.code ?? ""))}>Copy {group.code}</button>}
        </div>}
      </div>)}
      {directory.error && <p className="error" role="alert">{directory.error}</p>}
      {active?.role === "OWNER" && directory.data?.filter(member => member.status === "PENDING").map(member =>
        <div className="living-row" key={member.id}>
          <span>{member.name} requested to join {active.name}</span>
          <div className="living-buttons">{(["APPROVED", "REJECTED"] as const).map(status =>
            <button key={status} className="secondary" disabled={action.busy} onClick={() => void action.run(() =>
              api.patch(`/groups/${active.id}/members/${member.id}`, { status }))}>
              {status === "APPROVED" ? "Approve" : "Reject"}
            </button>)}</div>
        </div>)}
    </div>
    {bank.error && <p className="error" role="alert">{bank.error}</p>}
    {!bank.loading && !directory.loading && <form className="living-card form" onSubmit={saveBank}
      key={`${active?.id}:${currentBank?.bank_bin}:${currentBank?.bank_account_no}`}>
      <h2>Bank and VietQR</h2>
      {active && !active.qr_ready && <p role="status">Set up VietQR for {active.name} before logging or joining a split.</p>}
      <label>Bank BIN<input name="bank_bin" required pattern="[0-9]{6}" inputMode="numeric" defaultValue={currentBank?.bank_bin} /></label>
      <label>Account number<input name="bank_account_no" required pattern="[0-9]{4,32}" inputMode="numeric" defaultValue={currentBank?.bank_account_no} /></label>
      <label>Account holder<input name="bank_account_name" required maxLength={100} defaultValue={currentBank?.bank_account_name} /></label>
      <button className="primary" disabled={action.busy}>{active ? "Save to profile and selected group" : "Save to profile"}</button>
    </form>}
  </section>;
}
