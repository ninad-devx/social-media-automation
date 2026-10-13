import type { Channel } from "../../types/channel";
import type { MediaItem } from "../../types/publishing";
import { platformMeta } from "../../lib/platforms";
import { PlatformAvatar } from "../ui/PlatformAvatar";
import { describe } from "./SchedulePicker";

/**
 * Read-only visual preview of what will be sent.
 * It never modifies content - it renders exactly what the user typed.
 */
export function PostPreview({
  accountName,
  channel,
  content,
  media,
  publishMode,
  postType,
  date,
  time,
  timezone,
  emptyHint,
}: {
  accountName: string | null;
  channel: Channel | null;
  content: string;
  media: MediaItem[];
  publishMode: "NOW" | "SCHEDULE";
  /** Shown so the preview matches what will actually be sent. */
  postType?: string | null;
  date: string;
  time: string;
  timezone: string;
  emptyHint?: string;
}) {
  const meta = platformMeta(channel?.platform);
  const hasContent = content.trim().length > 0;
  const hasMedia = media.length > 0;

  if (!channel) {
    return (
      <div className="preview preview--empty">
        <div className="preview__placeholder">
          <IconEye />
          <p>{emptyHint ?? "Select an account and channel to see a preview."}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="preview">
      <div className="preview__header">
        <PlatformAvatar meta={meta} size={26} />
        <div className="preview__identity">
          <span className="preview__account">{accountName ?? channel.account_id}</span>
          <span className="preview__channel">
            {meta.label}
            {channel.username ? ` · ${channel.username}` : ""}
          </span>
        </div>
        <span className={`preview__mode ${publishMode === "NOW" ? "is-now" : "is-scheduled"}`}>
          {publishMode === "NOW" ? "Publish now" : "Scheduled"}
          {postType ? ` · ${postType.replace(/_/g, " ")}` : ""}
        </span>
      </div>

      <div className="preview__body">
        {hasContent ? (
          <p className="preview__text">{content}</p>
        ) : (
          <p className="preview__text preview__text--empty">
            Your post text will appear here.
          </p>
        )}

        {hasMedia && (
          <div className={`preview__media preview__media--${Math.min(media.length, 4)}`}>
            {/* Four items render, so the grid class is capped at four too. It
                was capped at three, which made the last cell overflow. */}
            {media.slice(0, 4).map((m, i) => (
              <span key={`${m.url}-${i}`} className="preview__media-item">
                {m.type === "video" ? (
                  // An actual <video>, not the word "Video".
                  //
                  // The previous version rendered a text placeholder for every
                  // non-image, so selecting Reel and attaching a video showed
                  // nothing to look at and there was no way to tell whether the
                  // right file had been picked. `preload="metadata"` is enough
                  // for the browser to fetch a frame without pulling the whole
                  // file, and the controls are off because this is a preview.
                  <video
                    className="preview__media-video"
                    src={m.preview_url || m.url}
                    preload="metadata"
                    muted
                    playsInline
                    onError={(e) =>
                      e.currentTarget.parentElement?.classList.add("is-broken")
                    }
                  />
                ) : (
                  <img
                    src={m.preview_url || m.url}
                    alt=""
                    loading="lazy"
                    onError={(e) => e.currentTarget.parentElement?.classList.add("is-broken")}
                  />
                )}
              </span>
            ))}
          </div>
        )}
      </div>

      {publishMode === "SCHEDULE" && (date || time) && (
        <div className="preview__footer">
          <IconClock />
          <span>{describe(date, time, timezone) ?? "Choose a date and time"}</span>
        </div>
      )}
    </div>
  );
}

function IconEye() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function IconClock() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}