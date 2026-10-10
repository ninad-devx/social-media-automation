/**
 * Settings shell (layout only).
 *
 * Owns the tab strip. Each tab is its own route so a deep link works and a
 * refresh keeps the user where they were. The AI tab navigates to the existing,
 * unchanged `/settings/ai` page.
 */
import { NavLink, Outlet } from "react-router-dom";

const TABS = [
  { to: "/settings", label: "Overview", end: true },
  { to: "/settings/integrations", label: "Buffer integrations", end: false },
  { to: "/settings/accounts", label: "Accounts & channels", end: false },
  { to: "/settings/ai", label: "AI providers", end: false },
];

export function SettingsLayout() {
  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Settings</h1>
          <p className="page__subtitle">
            Configure connections, brands and AI. Nothing here requires editing code, environment
            variables or an n8n workflow.
          </p>
        </div>
      </header>

      <nav className="tabs" aria-label="Settings sections">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) => `tab ${isActive ? "tab--active" : ""}`}
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      <div className="page__body">
        <Outlet />
      </div>
    </div>
  );
}
