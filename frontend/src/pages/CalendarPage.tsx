import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Button, Badge } from "../components/ui";
import { PlatformAvatar } from "../components/ui/PlatformAvatar";
import { platformMeta } from "../lib/platforms";
import { publishingApi } from "../services/publishing";
import { useAccounts } from "../hooks/useAccounts";
import { formatInZone, truncate } from "../lib/format";

/**
 * Calendar foundation.
 *
 * Shows scheduled publications grouped by day. This is deliberately our own
 * data from the backend, not a mirror of Buffer's calendar.
 */
export function CalendarPage() {
  const { data: accounts } = useAccounts();
  const [byDay, setByDay] = useState<Record<string, ScheduledItem[]>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    publishingApi
      .history({ limit: 200, }, controller.signal)
      .then((page) => {
        const scheduled = page.items.filter((i) => i.status === "scheduled" && i.scheduled_at);
        const grouped: Record<string, ScheduledItem[]> = {};
        for (const item of scheduled) {
          const day = formatInZone(item.scheduled_at, item.timezone, "yyyy-LL-dd");
          (grouped[day] ??= []).push({
            id: String(item.id),
            content: truncate(item.content_preview, 80),
            platform: item.platform,
            channel_id: item.channel_id,
            account_id: item.account_id,
            time: formatInZone(item.scheduled_at, item.timezone, "HH:mm"),
            timezone: item.timezone,
          });
        }
        Object.values(grouped).forEach((list) => list.sort((a, b) => a.time.localeCompare(b.time)));
        setByDay(grouped);
        setLoading(false);
      })
      .catch(() => {
        setByDay({});
        setLoading(false);
      });
    return () => controller.abort();
  }, []);

  const days = Object.keys(byDay).sort();
  const scheduledCount = days.reduce((n, d) => n + byDay[d].length, 0);

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Calendar</h1>
          <p className="page__subtitle">A simple view of everything queued to go out.</p>
        </div>
        <Link to="/">
          <Button variant="primary">Schedule a post</Button>
        </Link>
      </header>

      <Card>
        {!loading && scheduledCount === 0 && (
          <div className="cal__intro">
            <div className="cal__intro-icon">
              <IconCalendar />
            </div>
            <h3>Nothing scheduled yet</h3>
            <p>
              When you schedule a post it will appear here, grouped by day. This view is
              built from this workspace's own records.
            </p>
            <Link to="/">
              <Button variant="secondary">Go to Quick Publish</Button>
            </Link>
          </div>
        )}

        {loading && <div className="skeleton" style={{ height: 140 }} />}

        {scheduledCount > 0 && (
          <div className="cal">
            {days.map((day) => (
              <section key={day} className="cal__day">
                <h3 className="cal__day-label">{formatDayHeading(day)}</h3>
                <ul className="cal__list">
                  {byDay[day].map((item) => {
                    const meta = platformMeta(item.platform);
                    return (
                      <li key={item.id} className="cal__item">
                        <span className="cal__time mono">{item.time}</span>
                        <PlatformAvatar meta={meta} size={28} />
                        <div className="cal__body">
                          <span className="cal__text">{item.content}</span>
                          <span className="cal__meta">
                            {item.account_id} · {meta.label} · {item.timezone}
                          </span>
                        </div>
                        <Badge tone="info">scheduled</Badge>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
      </Card>

      {accounts && accounts.length > 0 && (
        <p className="page__footnote">
          Scheduling is delegated to the publishing engine — n8n returns immediately and
          Buffer handles the actual delivery time.
        </p>
      )}
    </div>
  );
}

interface ScheduledItem {
  id: string;
  content: string;
  platform: string;
  channel_id: string;
  account_id: string;
  time: string;
  timezone: string;
}

function formatDayHeading(isoDay: string): string {
  const [y, m, d] = isoDay.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  const today = new Date();
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(
    today.getDate(),
  ).padStart(2, "0")}`;

  if (isoDay === todayKey) return "Today";
  const tomorrow = new Date(today.getTime() + 86400000);
  const tKey = `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, "0")}-${String(
    tomorrow.getDate(),
  ).padStart(2, "0")}`;
  if (isoDay === tKey) return "Tomorrow";

  return date.toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
}

function IconCalendar() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="16" rx="2.5" />
      <path d="M8 3v4M16 3v4M3 10h18" />
    </svg>
  );
}