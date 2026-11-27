import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Menu, X, BarChart3, Bell, Building2, Landmark, LogOut, MessageCircle, Sparkles, Users } from "lucide-react";
import { type Group, realtimeUpdate, useAction, useResource } from "./lib/living";
import { enableAlerts, notify } from "./lib/notify";
import GroupBudgetInsights from "./components/GroupBudgetInsights";
import AiAdvisorPage from "./pages/AiAdvisorPage";
import GroupChatPage from "./pages/GroupChatPage";
import GroupHubPage from "./pages/GroupHubPage";
import LoginRegisterPage from "./pages/LoginRegisterPage";
import SmartDebtsAndQrPage from "./pages/SmartDebtsAndQrPage";
import { useAuthStore } from "./store/useAuthStore";
import { connectRealtime, useRealtimeStore } from "./store/useRealtimeStore";
import type { User } from "./types/api";

const OverviewAnalyticsPage = lazy(() => import("./pages/OverviewAnalyticsPage"));

const pages = [
  ["overview", "Tổng quan", BarChart3], ["groups", "Nhóm", Users], ["debts", "Công nợ & VietQR", Landmark],
  ["chat", "Trò chuyện nhóm", MessageCircle], ["advisor", "Trợ lý", Sparkles],
] as const;
type Page = typeof pages[number][0];

function Workspace({ user, token }: { user: User; token: string }) {
  const logout = useAuthStore(state => state.logout);
  const [menuOpen, setMenuOpen] = useState(false);
  const [page, setPage] = useState<Page>("overview");
  const [groupId, setGroupId] = useState<number>();
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState("");
  const [alerts, setAlerts] = useState(false);
  const action = useAction();
  const { status, error: realtimeError } = useRealtimeStore();
  const reload = useCallback(() => setRevision(value => value + 1), []);
  const groups = useResource<Group[]>("/groups", revision);
  const available = groups.data?.filter(group => group.status === "APPROVED") ?? [];
  const active = available.find(group => group.id === groupId) ?? available[0];
  useEffect(() => {
    const stop = connectRealtime(token, user, event => {
      const update = realtimeUpdate(event);
      if (!update) return;
      if (update.notice) {
        setNotice(update.notice);
        void notify("Shared Living", update.notice);
      }
      reload();
    });
    const expiry = window.setTimeout(logout, 30 * 60_000);
    const poll = window.setInterval(reload, 60_000);
    return () => { stop(); window.clearTimeout(expiry); window.clearInterval(poll); };
  }, [token, user, logout, reload]);
  useEffect(() => {
    if (!menuOpen) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuOpen(false); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [menuOpen]);
  return <div className="living-shell">
    <div className="mobile-bar"><strong>Shared Living</strong><button className="secondary" aria-expanded={menuOpen} aria-controls="workspace-navigation" onClick={() => setMenuOpen(!menuOpen)}><Menu aria-hidden="true" size={22}/> Menu nh?m</button></div>
    {menuOpen && <button className="nav-backdrop" aria-label="??ng menu" onClick={() => setMenuOpen(false)}/> }
    <aside id="workspace-navigation" className={`living-nav${menuOpen ? " is-open" : ""}`}>
      <button className="mobile-close secondary" onClick={() => setMenuOpen(false)}><X aria-hidden="true" size={22}/> ??ng menu</button>
      <div className="living-brand"><Building2 size={23} /><strong>Shared Living</strong></div>
      <nav aria-label="Workspace">{pages.map(([id, label, Icon]) => <button key={id}
        aria-current={page === id ? "page" : undefined} onClick={() => { setPage(id); setMenuOpen(false); }}><Icon size={19} />{label}</button>)}</nav>
      <div className="living-account"><strong>{user.name}</strong><button onClick={logout}><LogOut size={16} /> Đăng xuất</button></div>
    </aside>
    <main className="living-main">
      <header className="living-toolbar">
        <label>Nhóm đang chọn <select value={active?.id ?? ""} disabled={!available.length}
          onChange={event => setGroupId(Number(event.target.value))}>
          {!available.length && <option value="">Chưa có nhóm được duyệt</option>}
          {available.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
        </select></label>
        <button className="secondary" disabled={alerts || action.busy} onClick={() => void action.run(async () => {
          const allowed = await enableAlerts(); setAlerts(allowed); setNotice(allowed ? "Đã bật thông báo." : "Chưa được cấp quyền thông báo.");
        })}><Bell size={17} />{alerts ? "Đã bật thông báo" : "Bật thông báo"}</button>
      </header>
      {status !== "online" && <p className="app-notice" role="status">{realtimeError || "Đang kết nối lại để nhận cập nhật trực tiếp. Dữ liệu hiển thị có thể chưa mới nhất."}</p>}
      {notice && <div className="app-notice" role="status">{notice}<button className="secondary" onClick={() => setNotice("")}>Đóng</button></div>}
      {(groups.error || action.error) && <p className="error" role="alert">{groups.error || action.error}</p>}
      {page === "overview" && <Suspense fallback={<p role="status">Loading the overview…</p>}>
        <OverviewAnalyticsPage key={active?.id} group={active} revision={revision} reload={reload}
          userId={user.id} configure={() => setPage("groups")} />
      </Suspense>}
      {page === "groups" && <GroupHubPage key={active?.id} active={active} groups={groups.data ?? []} userId={user.id}
        select={setGroupId} reload={reload} revision={revision} />}
      {page === "advisor" && <div className="living-stack">
        {active && <GroupBudgetInsights key={active.id} group={active} revision={revision} reload={reload} />}
        <AiAdvisorPage revision={revision} reload={reload} />
      </div>}
      {page === "debts" && active && <SmartDebtsAndQrPage key={active.id} group={active} userId={user.id} revision={revision} reload={reload} />}
      {page === "chat" && active && <GroupChatPage key={active.id} group={active} userId={user.id} revision={revision} reload={reload} />}
      {(page === "debts" || page === "chat") && !active && <div className="living-card">
        <p>Select an approved group to continue.</p><button className="primary" onClick={() => setPage("groups")}>Go to groups</button>
      </div>}
    </main>
  </div>;
}

export default function LivingApp() {
  const { user, token } = useAuthStore();
  return user && token ? <Workspace key={user.id} user={user} token={token} /> : <LoginRegisterPage />;
}
