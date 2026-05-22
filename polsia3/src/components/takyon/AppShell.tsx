import Link from "next/link";
import { Activity, Building2, FileText, Gauge, MessagesSquare } from "lucide-react";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/dashboard">
          <span className="brand-mark">T</span>
          <span>Takyon</span>
        </Link>
        <nav className="nav-list" aria-label="Primary">
          <Link className="nav-item active" href="/dashboard">
            <Gauge size={17} /> Dashboard
          </Link>
          <span className="nav-item">
            <Building2 size={17} /> Companies
          </span>
          <span className="nav-item">
            <MessagesSquare size={17} /> Inbox
          </span>
          <span className="nav-item">
            <FileText size={17} /> Documents
          </span>
          <span className="nav-item">
            <Activity size={17} /> Runs
          </span>
        </nav>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
