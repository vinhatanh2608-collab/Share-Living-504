import { Banknote, BookOpen, House, LayoutDashboard, LogOut, MessagesSquare, QrCode } from "lucide-react";
import { useRoomStore } from "../store/useRoomStore";
import { useRealtimeStore } from "../store/useRealtimeStore";
import { initials } from "../lib/format";
import type { User } from "../types/api";

const links = [{ id: "dashboard", label: "Overview", Icon: LayoutDashboard }, { id: "directory", label: "VietQR directory", Icon: QrCode },
  { id: "chat", label: "Room chat", Icon: MessagesSquare }, { id: "study", label: "Study vault", Icon: BookOpen }] as const;
export type PageId = typeof links[number]["id"];

export default function Sidebar({ user, page, navigate, logout }: { user: User; page: PageId; navigate: (id: PageId) => void; logout: () => void }) {
  const status = useRealtimeStore(s => s.status);
  const unread = useRoomStore(s => s.unread);
  const due = useRoomStore(s => s.debts.filter(d => d.debtor_id === user.id && d.status === "open").length);
  return <aside className="sidebar">
    <div className="brand"><span><House size={21}/></span><div>Dormitory Desk<small>ROOM WORKSPACE</small></div></div>
    <div className="side-label">WORKSPACE</div>
    <nav aria-label="Workspace">{links.map(({ id, label, Icon }) => {
      const count = id === "chat" ? unread : id === "dashboard" ? due : 0;
      return <button key={id} aria-current={id === page ? "page" : undefined} onClick={() => navigate(id)}><Icon size={19}/>{label}{count > 0 && <b>{count}</b>}</button>;
    })}</nav>
    <div className="side-tip"><Banknote size={18}/><div>Payments stay private.<small>Only the room owner reviews receipts.</small></div></div>
    <div className="connection"><i className={status}/>{status === "online" ? "Live connection" : status === "connecting" ? "Connecting…" : "Offline · reconnecting"}</div>
    <div className="identity"><span className="avatar">{initials(user.name)}</span><div>{user.name}<small>{user.is_owner ? "Room owner" : "Roommate"}</small></div>
      <button className="icon" aria-label="Sign out" onClick={logout}><LogOut size={17}/></button></div>
  </aside>;
}
