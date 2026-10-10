import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Button, EmptyState, Badge, Spinner } from "../components/ui";
import { PlatformAvatar } from "../components/ui/PlatformAvatar";
import { platformMeta } from "../lib/platforms";
import { publishingApi } from "../services/publishing";
import { useAccounts } from "../hooks/useAccounts";
import type { PublishLogEntry } from "../types/publishing";
import { formatInZone, truncate } from "../lib/format";

export function PostsPage() {
  const { data: accounts } = useAccounts();
  const [accountFilter, setAccountFilter] = useState<string>("all");
  const [entries, setEntries] = useState<PublishLogEntry[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    publishingApi
      .history(
        { limit: 100, accountId: accountFilter === "all" ? undefined : accountFilter },
        controller.signal,
      )
      .then((page) => {
        setEntries(page.items);
        setLoading(false);
      })
      .catch(() => {
        setEntries([]);
        setLoading(false);
      });
    return () => controller.abort();
  }, [accountFilter]);

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Posts</h1>
          <p className="page__subtitle">Everything this workspace has published or scheduled.</p>
        </div>
        <Link to="/">
          <Button variant="primary">New post</Button>
        </Link>
      </header>

      <Card>
        <div className="posts__toolbar">
          <div className="posts__filters" role="group" aria-label="Filter by account">
            <button
              type="button"
              className={`chip ${accountFilter === "all" ? "is-active" : ""}`}
              onClick={() => setAccountFilter("all")}
            >
              All accounts
            </button>
            {(accounts ?? []).map((a) => (
              <button
                key={a.id}
                type="button"
                className={`chip ${accountFilter === a.id ? "is-active" : ""}`}
                onClick={() => setAccountFilter(a.id)}
              >
                {a.name}
              </button>
            ))}
          </div>
          {loading && <Spinner size={16} />}
        </div>

        {loading && !entries && (
          <div className="posts__list">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ height: 76 }} />
            ))}
          </div>
        )}

        {!loading && entries && entries.length === 0 && (
          <EmptyState
            icon={<IconInbox />}
            title="No posts yet"
            description="Published and scheduled posts will appear here as soon as you create them."
            action={
              <Link to="/">
                <Button variant="primary">Create your first post</Button>
              </Link>
            }
          />
        )}

        {entries && entries.length > 0 && (
          <ul className="posts__list">
            {entries.map((entry) => (
              <PostRow key={entry.id} entry={entry} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function PostRow({ entry }: { entry: PublishLogEntry }) {
  const meta = platformMeta(entry.platform);
  const zone = entry.timezone || "UTC";
  const tone =
    entry.status === "failed" ? "danger" : entry.status === "scheduled" ? "info" : "success";

  return (
    <li className="post-row">
      <PlatformAvatar meta={meta} size={34} />
      <div className="post-row__body">
        <div className="post-row__top">
          <span className="post-row__account">{entry.account_id}</span>
          <span className="post-row__dot">·</span>
          <span className="post-row__platform">{meta.label}</span>
          <Badge tone={tone}>{entry.status}</Badge>
        </div>
        <p className="post-row__text">{truncate(entry.content_preview)}</p>
      </div>
      <div className="post-row__meta">
        {entry.status === "scheduled" && entry.scheduled_at ? (
          <span title={formatInZone(entry.scheduled_at, zone, "cccc")}>
            {formatInZone(entry.scheduled_at, zone, "d LLL · h:mm a")}
          </span>
        ) : (
          <span>{formatInZone(entry.created_at, zone, "d LLL · h:mm a")}</span>
        )}
        {entry.media_count > 0 && (
          <span className="post-row__media">
            <IconImage /> {entry.media_count}
          </span>
        )}
      </div>
    </li>
  );
}

function IconInbox() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-6l-2 3h-4l-2-3H2" />
      <path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1Z" />
    </svg>
  );
}

function IconImage() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="9" cy="9" r="1.5" />
      <path d="m21 15-5-5L5 21" />
    </svg>
  );
}