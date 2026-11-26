import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { BarChart3, Bell, Building2, Landmark, LogOut, MessageCircle, Sparkles, Users } from "lucide-react";
import { type Group, realtimeUpdate, useAction, useResource } from "./lib/living";
import { enableAlerts, notify } from "./lib/notify";
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
  ["overview", "Overview", BarChart3], ["groups", "Groups", Users], ["debts", "Debts & VietQR", Landmark],
  ["chat", "Group chat", MessageCircle], ["advisor", "Advisor", Sparkles],
] as const;
type Page = typeof pages[number][0];

function Workspace({ user, token }: { user: User; token: string }) {
  const logout = useAuthStore(state => state.logout);
  const [page, setPage] = useState<Page>("overview");
  const [groupId, setGroupId] = useState<number>();
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState("");
  const [alerts, setAlerts] = useState(false);
  const action = useAction();
  const status = useRealtimeStore(state => state.status);
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
  return <div className="living-shell">
    <aside className="living-nav">
      <div className="living-brand"><Building2 size={23} /><strong>Shared Living</strong></div>
      <nav aria-label="Workspace">{pages.map(([id, label, Icon]) => <button key={id}
        aria-current={page === id ? "page" : undefined} onClick={() => setPage(id)}><Icon size={19} />{label}</button>)}</nav>
      <div className="living-account"><strong>{user.name}</strong><button onClick={logout}><LogOut size={16} /> Sign out</button></div>
    </aside>
    <main className="living-main">
      <header className="living-toolbar">
        <label>Active group <select value={active?.id ?? ""} disabled={!available.length}
          onChange={event => setGroupId(Number(event.target.value))}>
          {!available.length && <option value="">No approved group</option>}
          {available.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
        </select></label>
        <button className="secondary" disabled={alerts || action.busy} onClick={() => void action.run(async () => {
          const allowed = await enableAlerts(); setAlerts(allowed); setNotice(allowed ? "Desktop alerts enabled." : "Alerts were declined.");
        })}><Bell size={17} />{alerts ? "Alerts enabled" : "Enable alerts"}</button>
      </header>
      {status !== "online" && <p className="app-notice" role="status">Live updates are reconnecting. Displayed data may be out of date.</p>}
      {notice && <div className="app-notice" role="status">{notice}<button className="secondary" onClick={() => setNotice("")}>Dismiss</button></div>}
      {(groups.error || action.error) && <p className="error" role="alert">{groups.error || action.error}</p>}
      {page === "overview" && <Suspense fallback={<p role="status">Loading the overview…</p>}>
        <OverviewAnalyticsPage key={active?.id} group={active} revision={revision} reload={reload}
          userId={user.id} configure={() => setPage("groups")} />
      </Suspense>}
      {page === "groups" && <GroupHubPage key={active?.id} active={active} groups={groups.data ?? []} userId={user.id}
        select={setGroupId} reload={reload} revision={revision} />}
      {page === "advisor" && <AiAdvisorPage revision={revision} reload={reload} />}
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
