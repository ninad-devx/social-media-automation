import { Badge, Button } from "../ui";
import { PlatformAvatar } from "../ui/PlatformAvatar";
import { platformMeta } from "../../lib/platforms";
import { API_BASE } from "../../services/api";
import type { CampaignPost } from "../../types/ai";

/**
 * Per-channel visual preview for a campaign post.
 *
 * Mirrors the Quick Publish preview so a campaign reads the same way as a
 * single post: you always see the caption, the image and the platform before
 * anything is sent.
 */
export function PostPreviewCard({
  post,
  children,
  requiresMedia = false,
}: {
  post: CampaignPost;
  children?: React.ReactNode;
  /**
   * Supplied by the caller from the backend capability endpoint, so this
   * component keeps no platform table of its own.
   */
  requiresMedia?: boolean;
}) {
  const meta = platformMeta(post.platform);
  const text = post.effective_content?.trim() ?? "";
  const locked = post.publishing_status === "published" || post.publishing_status === "scheduled";

  return (
    <article className={`preview-card ${locked ? "is-locked" : ""}`}>
      <header className="preview-card__header">
        <PlatformAvatar meta={meta} size={30} />
        <div className="preview-card__identity">
          <span className="preview-card__account">{post.account_name}</span>
          <span className="preview-card__channel">
            {meta.label}
            {requiresMedia && !post.media.length && (
              <span className="preview-card__warn"> · needs an image</span>
            )}
          </span>
        </div>
        <div className="preview-card__badges">
          {post.is_edited && <Badge tone="info">Edited</Badge>}
          {post.publishing_status === "published" && <Badge tone="success">Published</Badge>}
          {post.publishing_status === "scheduled" && <Badge tone="success">Scheduled</Badge>}
          {post.publishing_status === "failed" && <Badge tone="danger">Failed</Badge>}
          {post.generation_status === "failed" && <Badge tone="danger">No draft</Badge>}
        </div>
      </header>

      <div className="preview-card__body">
        {text ? (
          <p className="preview-card__text">{text}</p>
        ) : (
          <p className="preview-card__text preview-card__text--empty">
            {post.generation_status === "failed"
              ? "No draft was generated for this channel."
              : "Nothing written yet."}
          </p>
        )}

        {post.media.length > 0 && (
          <div className={`preview-card__media preview-card__media--${Math.min(post.media.length, 3)}`}>
            {post.media.slice(0, 4).map((m, i) => (
              <span key={`${m.url}-${i}`} className="preview-card__media-item">
                {m.type === "video" ? (
                  <span className="preview-card__video">Video</span>
                ) : (
                  <img
                    src={previewSrc(m.url ?? "")}
                    alt={m.alt_text ?? ""}
                    loading="lazy"
                    onError={(e) => {
                      e.currentTarget.parentElement?.classList.add("is-broken");
                    }}
                  />
                )}
              </span>
            ))}
          </div>
        )}

        {post.hashtags.length > 0 && (
          <p className="preview-card__tags">
            {post.hashtags.map((t) => `#${t.replace(/^#/, "")}`).join(" ")}
          </p>
        )}
      </div>

      <footer className="preview-card__footer">
        <span className={overLimit(post) ? "preview-card__over" : ""}>
          {post.character_count}
          {post.character_limit ? ` / ${post.character_limit}` : ""} characters
        </span>
        {post.scheduled_at && (
          <span>Scheduled {new Date(post.scheduled_at).toLocaleString()}</span>
        )}
        {post.external_link && (
          <a href={post.external_link} target="_blank" rel="noreferrer">
            View post
          </a>
        )}
      </footer>

      {children && <div className="preview-card__actions">{children}</div>}
    </article>
  );
}

/** What this platform accepts, so the picker never offers the impossible. */
export interface MediaCaps {
  platform: string;
  label: string;
  requires_media: boolean;
  supports_image: boolean;
  supports_video: boolean;
  supports_carousel: boolean;
  max_assets: number | null;
  text_limit: number | null;
}

export function mediaCapNote(caps: MediaCaps | null): string {
  if (!caps) return "";
  if (caps.requires_media) return `${caps.label} needs at least one image or video.`;
  if (caps.supports_carousel && (caps.max_assets ?? 0) > 1) {
    return `${caps.label} supports up to ${caps.max_assets} images in a carousel.`;
  }
  return `${caps.label} accepts one attachment.`;
}

/** A responsive grid of previews, one per channel. */
export function PreviewGrid({ children }: { children: React.ReactNode }) {
  return <div className="preview-grid">{children}</div>;
}

/**
 * Images are stored with the URL Buffer downloads, which may point at a
 * tunnel. Browsers get a direct API URL so thumbnails always render.
 */
export function previewSrc(publicUrl: string): string {
  if (!publicUrl) return "";
  const match = publicUrl.match(/^(https?:\/\/[^/]+)(\/.*)$/);
  if (!match) return publicUrl;
  // Keep any path prefix the deployment uses (e.g. /api/media).
  return `${API_BASE}${match[2]}`;
}

function overLimit(post: CampaignPost): boolean {
  return Boolean(post.character_limit && post.character_count > post.character_limit);
}

/** Small inline status label for posts that are not in a normal state. */
export function GenerationPill({ post }: { post: CampaignPost }) {
  if (post.generation_status === "generated") return null;
  return (
    <span className="pill-link">
      {post.generation_status}
      {post.error_message ? ` — ${post.error_message}` : ""}
    </span>
  );
}

export { Button };