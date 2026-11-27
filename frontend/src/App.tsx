import { useEffect, useState } from "react";
import { Bell, X } from "lucide-react";
import Sidebar, { type PageId } from "./components/Sidebar";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import QrDirectoryPage from "./pages/QrDirectoryPage";
import ChatPage from "./pages/ChatPage";
import StudyVaultPage from "./pages/StudyVaultPage";
import { useAuthStore } from "./store/useAuthStore";
import { useRoomStore } from "./store/useRoomStore";
import { connectRealtime, useRealtimeStore } from "./store/useRealtimeStore";
import { enableAlerts, notify } from "./lib/notify";
import { isOverdue } from "./lib/format";
import type { User } from "./types/api";
import LivingApp from "./LivingApp";

function Workspace({ user, token }: { user: User; token: string }) {
  const [page, setPage] = useState<PageId>("dashboard");
  const [notice, setNotice] = useState("");
  const [notifications, setNotifications] = useState(false);
  const { status: connection, error: realtimeError } = useRealtimeStore();
  const logout = useAuthStore(s => s.logout);
  useEffect(() => {
    const room = useRoomStore.getState();
    void room.refresh();
    const cleanup = connectRealtime(token, user);
    const refresh = setInterval(() => void room.refresh(), 60000);
    // Match lifetime of the in-memory access token and backend WebSocket session.
    const expire = setTimeout(logout, 30 * 60_000);
    return () => { clearInterval(refresh); clearTimeout(expire); cleanup(); room.reset(); };
  }, [token, user, logout]);
  async function enable() {
    try {
      const allowed = await enableAlerts();
      setNotifications(allowed);
      setNotice(allowed ? "Desktop alerts enabled." : "Notifications were not permitted by your system.");
      const n = useRoomStore.getState().debts.filter(d => d.debtor_id === user.id && d.status === "open" && isOverdue(d.due_at)).length;
      if (allowed && n) void notify("Overdue room debts", `You have ${n} overdue ${n === 1 ? "debt" : "debts"}.`);
    } catch { setNotice("Desktop alerts are unavailable."); }
  }
  return <div className="shell"><Sidebar user={user} page={page} navigate={setPage} logout={logout}/>
    <main className="workspace" id="main-content"><div className="topbar"><span><i/> DORM ROOM / SHARED WORKSPACE</span>
      <button className="secondary" onClick={() => void enable()} disabled={notifications}><Bell size={16}/>{notifications ? "Alerts enabled" : "Enable desktop alerts"}</button></div>
      {connection !== "online" && <div className="app-notice" role="status">
        {realtimeError || "Đã mất kết nối cập nhật trực tiếp. Hãy kiểm tra kết nối mạng."}
      </div>}
      {notice && <div className="app-notice" role="status">{notice}<button className="icon" onClick={() => setNotice("")} aria-label="Dismiss alert"><X size={16}/></button></div>}
      {page === "dashboard" && <DashboardPage user={user}/>}
      {page === "directory" && <QrDirectoryPage user={user}/>}
      {page === "chat" && <ChatPage user={user}/>}
      {page === "study" && <StudyVaultPage user={user}/>}
    </main></div>;
}

function LegacyApp() {
  const { user, token } = useAuthStore();
  return user && token ? <Workspace key={user.id} user={user} token={token}/> : <LoginPage/>;
}

export default function App() {
  return import.meta.env.VITE_LEGACY_ROOM === "true" ? <LegacyApp /> : <LivingApp />;
}
