import { useState, type FormEvent } from "react";
import { Copy, Settings, Users } from "lucide-react";
import { api } from "../lib/api";
import { type Bank, type Group, type Member, useAction, useResource } from "../lib/living";
import GroupExpenses from "../components/GroupExpenses";
import SmartDebtsAndQrPage from "./SmartDebtsAndQrPage";

export default function GroupHubPage({ groups, active, select, revision, reload, userId }: {
  groups: Group[]; active: Group | undefined; select: (groupId: number) => void;
  revision: number; reload: () => void; userId: number;
}) {
  const [setup, setSetup] = useState(!active);
  const [notice, setNotice] = useState("");
  const action = useAction(reload);
  const directory = useResource<Member[]>(active ? `/groups/${active.id}/members` : null, revision);
  const bank = useResource<Bank | null>(setup ? "/users/me/bank-info" : null, revision);
  const members = directory.data ?? [];
  const currentBank = members.find(member => member.id === userId)?.qr ?? bank.data;
  function groupForm(event: FormEvent<HTMLFormElement>, join: boolean) {
    event.preventDefault();
    const form = event.currentTarget;
    const value = String(new FormData(form).get("group")).trim();
    void action.run(async () => {
      const { data } = await api.post<Group>(join ? "/groups/join" : "/groups", join ? { group_code: value } : { name: value });
      if (data.status === "APPROVED") select(data.id);
      setNotice(data.status === "PENDING" ? "Đã gửi yêu cầu. Vui lòng chờ chủ nhóm duyệt." :
        data.status === "REJECTED" ? "Chủ nhóm đã từ chối yêu cầu tham gia." : "Đã chọn nhóm.");
      form.reset();
    });
  }
  function saveBank(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    void action.run(async () => {
      await api.put("/users/me/bank-info", values);
      if (active) await api.put(`/groups/${active.id}/members/me/qr`, values);
      setNotice("Đã lưu thông tin VietQR.");
    });
  }
  return <section className="living-stack">
    <div className="living-heading"><h1>{active?.name ?? "Nhóm của bạn"}</h1>
      {active?.code && <button className="secondary" onClick={async () => {
        try { await navigator.clipboard.writeText(active.code!); setNotice("Đã sao chép mã mời."); }
        catch { setNotice(`Hãy sao chép mã mời: ${active.code}`); }
      }}><Copy size={16}/> Mã mời · {active.code}</button>}
    </div>
    {notice && <p className="app-notice" role="status">{notice}</p>}
    {(action.error || directory.error) && <p className="error" role="alert">{action.error || directory.error}</p>}
    {active && <>
      <SmartDebtsAndQrPage group={active} members={members} userId={userId} revision={revision} reload={reload}/>
      <GroupExpenses groupId={active.id} members={members} userId={userId} revision={revision} reload={reload}/>
      <section className="living-card"><h2><Users size={18}/> Thành viên và yêu cầu tham gia</h2>
        <div className="group-members">{members.filter(m => m.status === "APPROVED").map(m =>
          <div key={m.id}><strong>{m.name}</strong><small>{m.qr ? "Đã có VietQR" : "Chưa có VietQR"}</small></div>)}</div>
        {active.role === "OWNER" && members.filter(m => m.status === "PENDING").map(m =>
          <div className="living-row" key={m.id}><span>{m.name} muốn tham gia nhóm</span>
            <div className="living-buttons">{(["APPROVED", "REJECTED"] as const).map(status =>
              <button key={status} className="secondary" disabled={action.busy} onClick={() => void action.run(() =>
                api.patch(`/groups/${active.id}/members/${m.id}`, { status }))}>{status === "APPROVED" ? "Chấp nhận" : "Từ chối"}</button>)}</div>
          </div>)}
      </section>
    </>}
    <details className="living-card group-setup" open={setup} onToggle={e => setSetup(e.currentTarget.open)}>
      <summary><Settings size={18}/> Quản lý &amp; Cài đặt nhóm</summary>
      <div className="living-grid">
        <form className="form" onSubmit={e => groupForm(e, false)}><h2>Tạo nhóm</h2><label>Tên nhóm<input name="group" maxLength={100} required/></label>
          <button className="primary" disabled={action.busy}>Tạo nhóm</button></form>
        <form className="form" onSubmit={e => groupForm(e, true)}><h2>Tham gia nhóm</h2><label>Mã mời<input name="group" maxLength={16} required/></label>
          <button className="secondary" disabled={action.busy}>Gửi yêu cầu</button></form>
      </div>
      {groups.filter(g => g.status !== "APPROVED").map(g => <p key={g.id}>{g.name} · {g.status === "PENDING" ? "Chờ duyệt" : "Đã từ chối"}</p>)}
      {bank.error && <p className="error" role="alert">{bank.error}</p>}
      {setup && !bank.loading && !directory.loading && <form key={JSON.stringify(currentBank)} className="form" onSubmit={saveBank}>
        <h2>Ngân hàng và VietQR của bạn</h2>
        <label>Mã BIN ngân hàng<input name="bank_bin" required pattern="[0-9]{6}" inputMode="numeric" defaultValue={currentBank?.bank_bin}/></label>
        <label>Số tài khoản<input name="bank_account_no" required pattern="[0-9]{4,32}" inputMode="numeric" defaultValue={currentBank?.bank_account_no}/></label>
        <label>Chủ tài khoản<input name="bank_account_name" required maxLength={100} defaultValue={currentBank?.bank_account_name}/></label>
        <button className="primary" disabled={action.busy}>{active ? "Lưu vào hồ sơ và nhóm này" : "Lưu vào hồ sơ"}</button>
      </form>}
    </details>
  </section>;
}
