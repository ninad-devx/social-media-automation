import type { PublishResponse } from "../../types/publishing";
import { platformMeta } from "../../lib/platforms";
import { PlatformAvatar } from "../ui/PlatformAvatar";
import { Button } from "../ui";
import { formatInZone } from "../../lib/format";

/** Clean success state. Technical ids stay secondary and de-emphasised. */
export function PublishSuccess({
  result,
  onDismiss,
}: {
  result: PublishResponse;
  onDismiss: () => void;
}) {
  const meta = platformMeta(result.platform);
  const scheduled = result.status === "scheduled";

  return (
    <div className={`result result--success`} role="status" aria-live="polite">
      <div className="result__top">
        <span className="result__check" aria-hidden="true">
          <IconCheck />
        </span>
        <div className="result__heading">
          <h3 className="result__title">{scheduled ? "Post scheduled" : "Published successfully"}</h3>
          <p className="result__sub">
            <PlatformAvatar meta={meta} size={18} />
            <span>
              {result.account_name} · {result.channel_name ?? meta.label}
            </span>
          </p>
        </div>
        <button
          type="button"
          className="result__close"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          <IconX />
        </button>
      </div>

      {scheduled && result.scheduled_at && (
        <div className="result__detail">
          <IconCalendar />
          <div>
            <p className="result__detail-label">Goes out</p>
            <p className="result__detail-value">
              {formatInZone(result.scheduled_at, result.timezone ?? "UTC", "cccc, d LLLL yyyy")}
              <span className="result__detail-time">
                {" · "}
                {formatInZone(result.scheduled_at, result.timezone ?? "UTC", "h:mm a ZZZZ")}
              </span>
            </p>
          </div>
        </div>
      )}

      {result.external_link && (
        <a
          className="result__link"
          href={result.external_link}
          target="_blank"
          rel="noopener noreferrer"
        >
          View on {meta.label}
          <IconExternal />
        </a>
      )}

      <div className="result__footer">
        {result.buffer_publication_id && (
          <span className="result__meta" title="Publishing engine reference">
            Publication ID <span className="mono">{result.buffer_publication_id}</span>
          </span>
        )}
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Write another post
        </Button>
      </div>
    </div>
  );
}

/** Friendly failure state. Only the backend's user-safe message is shown. */
export function PublishError({
  message,
  code,
  requestId,
  onRetry,
  onDismiss,
}: {
  message: string;
  code: string;
  requestId: string;
  onRetry?: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="result result--error" role="alert" aria-live="assertive">
      <div className="result__top">
        <span className="result__alert" aria-hidden="true">
          <IconAlert />
        </span>
        <div className="result__heading">
          <h3 className="result__title">Something went wrong</h3>
          <p className="result__message">{message}</p>
        </div>
        <button
          type="button"
          className="result__close"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          <IconX />
        </button>
      </div>

      <div className="result__footer">
        <span className="result__meta" title="Reference for support">
          <span className="mono">{code}</span>
          {requestId && (
            <>
              {" · "}
              <span className="mono">{requestId}</span>
            </>
          )}
        </span>
        <div className="result__actions">
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
          {onRetry && (
            <Button size="sm" variant="secondary" onClick={onRetry}>
              Try again
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function IconCheck() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" />
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

function IconAlert() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v5M12 16h.01" />
    </svg>
  );
}

function IconCalendar() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="16" rx="2.5" />
      <path d="M8 3v4M16 3v4M3 10h18" />
    </svg>
  );
}

function IconExternal() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3" />
    </svg>
  );
}