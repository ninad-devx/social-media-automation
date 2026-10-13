import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Button, EmptyState, Badge, Spinner } from "../components/ui";
import { PlatformAvatar } from "../components/ui/PlatformAvatar";
import { platformMeta } from "../lib/platforms";
import { publishingApi } from "../services/publishing";
import { useAccounts } from "../hooks/useAccounts";
import type { PublishLogEntry } from "../types/publishing";
import { formatInZone, truncate } from "../lib/format";

const PAGE_SIZE = 25;

export function PostsPage() {
  const { data: accounts } = useAccounts();
  const [accountFilter, setAccountFilter] = useState<string>("all");
  const [entries, setEntries] = useState<PublishLogEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [clearing, setClearing] = useState(false);

  const load = useCallback(
    async (nextOffset: number, signal?: AbortSignal) => {
      setLoading(true);
      // Cleared up front, so a failure on one filter cannot leave a stale banner
      // sitting over results that loaded fine afterwards.
      setError(null);
      try {
        const page = await publishingApi.history(
          {
            limit: PAGE_SIZE,
            offset: nextOffset,
            accountId: accountFilter === "all" ? undefined : accountFilter,
          },
          signal,
        );
        setEntries(page.items);
        setTotal(page.total);
        setOffset(nextOffset);
        setLoading(false);
      } catch (err: unknown) {
        if (signal?.aborted) return;
        setError(err instanceof Error ? err.message : "Could not load publishing history.");
        setEntries([]);
        setLoading(false);
      }
    },
    [accountFilter],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(0, controller.signal);
    return () => controller.abort();
  }, [load]);

  const changeFilter = (value: string) => {
    setAccountFilter(value);
    setOffset(0);
    setNotice(null);
  };

  /**
   * Remove one row.
   *
   * Says plainly that this only clears the local record. Deleting a row for a
   * post that really went out does not un-post it, and the confirmation should
   * not leave that ambiguous.
   */
  const removeEntry = async (entry: PublishLogEntry) => {
    const ok = window.confirm(
      `Remove this entry from your history?\n\n` +
        `${truncate(entry.content_preview, 60)}\n\n` +
        `This only removes the record from this app. Nothing is deleted or ` +
        `edited on ${platformMeta(entry.platform).label} - a post that already went ` +
        `out stays live.`,
    );
    if (!ok) return;

    setRemoving(entry.id);
    setError(null);
    setNotice(null);
    try {
      await publishingApi.deleteHistoryEntry(entry.id);
      setNotice("History entry removed.");
      // Stepping back a page keeps the list from ending on an empty final page.
      const nextOffset = entries && entries.length === 1 && offset > 0 ? offset - PAGE_SIZE : offset;
      await load(nextOffset);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove that entry.");
    } finally {
      setRemoving(null);
    }
  };

  const clearFiltered = async () => {
    const scope =
      accountFilter === "all"
        ? "every brand"
        : `the "${accounts?.find((a) => a.id === accountFilter)?.name ?? accountFilter}" brand`;
    const ok = window.confirm(
      `Remove all ${total} history ${total === 1 ? "entry" : "entries"} for ${scope}?\n\n` +
        `This only clears the records in this app. Nothing is deleted or edited on ` +
        `any social channel. This cannot be undone.`,
    );
    if (!ok) return;

    setClearing(true);
    setError(null);
    setNotice(null);
    try {
      const result = accountFilter === "all"
        ? await clearAllConfirmed()
        : await publishingApi.clearHistory({ accountId: accountFilter });
      setNotice(`Removed ${result.deleted} ${result.deleted === 1 ? "entry" : "entries"}.`);
      await load(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear history.");
    } finally {
      setClearing(false);
    }
  };

  /**
   * The backend deliberately refuses an unscoped clear, so "All brands" is
   * cleared brand by brand rather than by inventing a wildcard.
   */
  const clearAllConfirmed = async () => {
    const brands = accounts ?? [];
    if (brands.length === 0) return { deleted: 0 };
    let deleted = 0;
    for (const brand of brands) {
      const result = await publishingApi.clearHistory({ accountId: brand.id });
      deleted += result.deleted;
    }
    return { deleted };
  };

  const hasMore = entries !== null && offset + entries.length < total;

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
              onClick={() => changeFilter("all")}
            >
              All accounts
            </button>
            {(accounts ?? []).map((a) => (
              <button
                key={a.id}
                type="button"
                className={`chip ${accountFilter === a.id ? "is-active" : ""}`}
                onClick={() => changeFilter(a.id)}
              >
                {a.name}
              </button>
            ))}
          </div>
          <div className="row-actions">
            {loading && <Spinner size={16} />}
            {total > 0 && (
              <Button
                size="sm"
                variant="ghost"
                loading={clearing}
                onClick={() => void clearFiltered()}
              >
                Remove history
              </Button>
            )}
          </div>
        </div>

        {notice && (
          <p className="notice notice--ok" role="status">
            {notice}
          </p>
        )}

        {loading && !entries && (
          <div className="posts__list">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ height: 76 }} />
            ))}
          </div>
        )}

        {error && (
          <p className="notice notice--error" role="alert">
            {error}
          </p>
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
              <PostRow
                key={entry.id}
                entry={entry}
                busy={removing === entry.id}
                onRemove={() => void removeEntry(entry)}
              />
            ))}
          </ul>
        )}

        {entries && entries.length > 0 && total > PAGE_SIZE && (
          <div className="posts__toolbar">
            <span className="muted">
              Showing {offset + 1}-{Math.min(offset + entries.length, total)} of {total}
            </span>
            <div className="row-actions">
              <Button
                size="sm"
                variant="ghost"
                disabled={offset === 0 || loading}
                onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={!hasMore || loading}
                onClick={() => void load(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

function PostRow({
  entry,
  busy,
  onRemove,
}: {
  entry: PublishLogEntry;
  busy: boolean;
  onRemove: () => void;
}) {
  const meta = platformMeta(entry.platform);
  const zone = entry.timezone || "UTC";
  const failed = entry.status === "failed";
  const tone = failed ? "danger" : entry.status === "scheduled" ? "info" : "success";
  // A failed attempt with nothing to show for it was the audit's complaint:
  // a red "failed" and no reason, so nobody could tell what to fix.
  const reason = entry.error_message || entry.error_code;

  return (
    <li className="post-row">
      <PlatformAvatar meta={meta} size={34} />
      <div className="post-row__body">
        <div className="post-row__top">
          <span className="post-row__account">{entry.account_name ?? entry.account_id}</span>
          <span className="post-row__dot">·</span>
          <span className="post-row__platform">{meta.label}</span>
          <Badge tone={tone}>{entry.status}</Badge>
          {entry.post_type && (
            <Badge tone="neutral">{entry.post_type.replace(/_/g, " ")}</Badge>
          )}
        </div>
        <p className="post-row__text">{truncate(entry.content_preview)}</p>
        {failed && reason && (
          <p className="post-row__error" title={reason}>
            {reason}
          </p>
        )}
        {entry.external_link && (
          <a
            className="post-row__link"
            href={entry.external_link}
            target="_blank"
            rel="noreferrer noopener"
          >
            View on {meta.label}
          </a>
        )}
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
        <button
          type="button"
          className="media__remove"
          onClick={onRemove}
          disabled={busy}
          aria-label={`Remove history entry: ${truncate(entry.content_preview, 40)}`}
          title="Remove from history"
        >
          <IconX />
        </button>
      </div>
    </li>
  );
}

function IconInbox() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-6l-2 3h-4l-2-3H2" />
      <path d="M5.5 5.1 2 12v6a6 6 0 0 0 2 2h16a6 6 0 0 0 2-2v-6l-3.5-6.9A6 6 0 0 0 13 4H7.2a6 6 0 0 0-5.7 4.1Z" />
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

function IconX() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}