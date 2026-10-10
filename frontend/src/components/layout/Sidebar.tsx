import { NavLink } from "react-router-dom";

const NAV: Array<{ to: string; label: string; icon: () => React.JSX.Element; end: boolean }> = [
  { to: "/", label: "Quick Publish", icon: IconBolt, end: true },
  { to: "/ai", label: "AI Automation", icon: IconSparkle, end: false },
  { to: "/campaigns", label: "Campaigns", icon: IconWand, end: false },
  { to: "/dashboard", label: "Dashboard", icon: IconGrid, end: false },
  { to: "/posts", label: "Posts", icon: IconList, end: false },
  { to: "/calendar", label: "Calendar", icon: IconCalendar, end: false },
  { to: "/accounts", label: "Accounts", icon: IconUsers, end: false },
  { to: "/settings/ai", label: "AI Settings", icon: IconKey, end: false },
  { to: "/settings", label: "Settings", icon: IconSettings, end: false },
];

export function Sidebar({ collapsed }: { collapsed: boolean }) {
  return (
    <nav className="sidebar" aria-label="Main navigation">
      <div className="sidebar__brand">
        <span className="brand__mark" aria-hidden="true">
          <img src="/logo.png" alt="" width={30} height={30} />
        </span>
        {!collapsed && (
          <span className="brand__text">
            <span className="brand__name">Geekssort</span>
            <span className="brand__tag">Social OS</span>
          </span>
        )}
      </div>

      <ul className="sidebar__nav">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={end}
              className={({ isActive }) => `navlink ${isActive ? "is-active" : ""}`}
              title={collapsed ? label : undefined}
            >
              <Icon />
              {!collapsed && <span>{label}</span>}
            </NavLink>
          </li>
        ))}
      </ul>

      <div className="sidebar__footer">
        {!collapsed && (
          <div className="engine-card">
            <div className="engine-card__row">
              <span className="engine-card__dot" aria-hidden="true" />
              <span className="engine-card__label">Publishing engine</span>
            </div>
            <p className="engine-card__meta">n8n → Buffer</p>
          </div>
        )}
      </div>
    </nav>
  );
}

/* ---- icons (24px stroke, 1.75 weight) ---- */
function IconBolt() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2 4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5Z" />
    </svg>
  );
}
function IconGrid() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  );
}
function IconList() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" />
    </svg>
  );
}
function IconCalendar() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="16" rx="2.5" /><path d="M8 3v4M16 3v4M3 10h18" />
    </svg>
  );
}
function IconUsers() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}
function IconSettings() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6h.09A1.65 1.65 0 0 0 10 3.09V3a2 2 0 0 1 4 0v.09A1.65 1.65 0 0 0 15 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.2.63.78 1.06 1.44 1.06H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
    </svg>
  );
}
function IconSparkle() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" />
    </svg>
  );
}
function IconWand() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 4V2M15 22v-2M4.9 4.9 3.5 3.5M20.5 20.5 19.1 19.1M4 15H2M22 15h-2M6.3 17.7 4.9 19.1M19.1 4.9l-1.4-1.4" />
      <path d="m3 21 12-12 3 3L6 24Z" transform="translate(0 -2)" />
    </svg>
  );
}
function IconKey() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="7.5" cy="15.5" r="4.5" /><path d="M10.7 12.3 21 2m-4 4 3 3m-6-1 3 3" />
    </svg>
  );
}