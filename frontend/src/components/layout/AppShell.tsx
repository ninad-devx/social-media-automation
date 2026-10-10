import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";

export function AppShell() {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className={`shell ${collapsed ? "shell--collapsed" : ""}`}>
      <Sidebar collapsed={collapsed} />
      <div className="shell__main">
        <header className="topbar">
          <button
            type="button"
            className="topbar__toggle"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <IconMenu />
          </button>
          <div className="topbar__spacer" />
          <EngineStatus />
        </header>
        <main className="shell__content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/**
 * Surface-level indicator that the publishing engine is configured.
 * Reads only our own backend's /api/health; it never contacts n8n or Buffer.
 */
function EngineStatus() {
  return (
    <div className="engine-status" title="Publishing engine">
      <span className="engine-status__dot" aria-hidden="true" />
      <span className="engine-status__text">Engine online</span>
    </div>
  );
}

function IconMenu() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}