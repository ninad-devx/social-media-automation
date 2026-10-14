import { useEffect, useRef, useState } from "react";
import type { MediaItem, MediaType } from "../../types/publishing";
import {
  deleteMedia,
  limitForFile,
  uploadMediaWithHandle,
  type UploadHandle,
} from "../../services/media";
import { useUploadLimits } from "../../hooks/useUploadLimits";

const IMAGE_ACCEPT = "image/png,image/jpeg,image/webp,image/gif";
const VIDEO_ACCEPT = "video/mp4,video/quicktime,video/webm";

interface Pending {
  id: string;
  name: string;
  percent: number;
  previewUrl: string;
  isVideo: boolean;
}

/**
 * Media attachments for a post.
 *
 * Two ways in:
 *   1. pick or drop a file from your computer (uploaded to our backend)
 *   2. paste a public URL (for media that already lives somewhere)
 *
 * Both produce the same `MediaItem`, so everything downstream - the preview,
 * the publish request, the n8n workflow - is identical.
 *
 * `maxItems` is the platform's real limit, supplied by the capability endpoint.
 * The previous default of 10 let the operator attach ten images to LinkedIn or
 * YouTube, which accept exactly one, and the failure only appeared after
 * writing the post.
 *
 * Buffer downloads media from a URL, so a locally uploaded file needs a
 * publicly reachable address before it can actually be published. The backend
 * tells us whether the returned URL qualifies, and we surface that here
 * rather than letting the publish fail silently.
 *
 * The size ceiling is read from the server rather than hardcoded, and it is not
 * one number: an image and a video get different limits, because a 25 MB cap
 * meant for images was also rejecting every Facebook Reel worth uploading.
 */
export function MediaPicker({
  media,
  onChange,
  disabled = false,
  error,
  requiresMedia = false,
  maxItems = 10,
  acceptsImage = true,
  acceptsVideo = true,
}: {
  media: MediaItem[];
  onChange: (media: MediaItem[]) => void;
  disabled?: boolean;
  error?: string | null;
  requiresMedia?: boolean;
  maxItems?: number | null;
  acceptsImage?: boolean;
  acceptsVideo?: boolean;
}) {
  const [pending, setPending] = useState<Pending[]>([]);
  const [urlDraft, setUrlDraft] = useState("");
  const [urlType, setUrlType] = useState<MediaType>(acceptsVideo && !acceptsImage ? "video" : "image");
  const [notice, setNotice] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const limits = useUploadLimits();

  // In-flight uploads, so unmount can cancel them. Without this, navigating
  // away mid-upload left the request running and then setState on a dead
  // component.
  const inFlight = useRef<Set<UploadHandle>>(new Set());
  useEffect(() => {
    const handles = inFlight.current;
    return () => {
      for (const handle of handles) handle.cancel();
      handles.clear();
    };
  }, []);

  // Object URLs are revoked when the row disappears. They pin the whole file in
  // memory, so a page with a few large uploads was leaking hundreds of MB.
  useEffect(() => {
    const urls = pending.map((p) => p.previewUrl).filter(Boolean);
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
    };
  }, [pending]);

  const limit = maxItems === null || maxItems === undefined ? Number.MAX_SAFE_INTEGER : maxItems;
  const full = media.length >= limit;

  const accept = [acceptsImage && IMAGE_ACCEPT, acceptsVideo && VIDEO_ACCEPT]
    .filter(Boolean)
    .join(",");
  const acceptLabel = [
    acceptsImage ? "PNG, JPG, GIF, WEBP" : null,
    acceptsVideo ? "MP4" : null,
  ]
    .filter(Boolean)
    .join(", ");

  // One hint for both, so it never claims a ceiling the server has moved past.
  const ceiling = acceptsImage && acceptsVideo
    ? `images up to ${formatBytes(limits.image_max_bytes)}, videos up to ${formatBytes(limits.video_max_bytes)}`
    : acceptsVideo
      ? `up to ${formatBytes(limits.video_max_bytes)}`
      : `up to ${formatBytes(limits.image_max_bytes)}`;

  // ---- uploads ------------------------------------------------------------
  async function handleFiles(files: FileList | File[]) {
    setLocalError(null);
    setNotice(null);

    const room = limit - media.length;
    if (room <= 0) {
      setLocalError(`This channel takes at most ${limit} attachment${limit === 1 ? "" : "s"}.`);
      return;
    }

    for (const file of Array.from(files).slice(0, room)) {
      const isImage = file.type.startsWith("image/");
      const isVideo = file.type.startsWith("video/");

      const maxBytes = limitForFile(file, limits);
      if (file.size > maxBytes) {
        // Says which kind was measured and what the ceiling is, because "too
        // large" with no numbers is the message that sent this to support.
        setLocalError(
          `${file.name} is ${formatBytes(file.size)}. The limit for ${
            isVideo ? "a video" : "an image"
          } is ${formatBytes(maxBytes)}.`,
        );
        continue;
      }
      if (!isImage && !isVideo) {
        setLocalError(`${file.name} isn't a supported image or video.`);
        continue;
      }
      if (isImage && !acceptsImage) {
        setLocalError(`${file.name} is an image, which this format does not accept.`);
        continue;
      }
      if (isVideo && !acceptsVideo) {
        setLocalError(`${file.name} is a video, which this format does not accept.`);
        continue;
      }

      const id = `${file.name}-${file.size}-${file.lastModified}`;
      // Both kinds get an object URL. Previously only images did, so a video
      // had no local address at any point and its thumbnail was a generic icon
      // both during the upload and afterwards.
      const previewUrl = URL.createObjectURL(file);

      setPending((p) => [...p, { id, name: file.name, percent: 0, previewUrl, isVideo }]);

      const handle = uploadMediaWithHandle(
        file,
        (progress) => {
          setPending((p) =>
            p.map((item) => (item.id === id ? { ...item, percent: progress.percent } : item)),
          );
        },
        limits,
      );
      inFlight.current.add(handle);

      try {
        const uploaded = await handle.promise;

        if (!uploaded || typeof uploaded.kind !== "string") {
          throw new Error("The upload finished but the server sent back nothing usable.");
        }

        const kind = uploaded.kind as MediaType;
        onChange([
          ...mediaRef.current,
          {
            type: kind,
            url: uploaded.url,
            preview_url: uploaded.preview_url ?? uploaded.url,
            asset_id: uploaded.asset_id,
            filename: uploaded.filename || file.name,
            alt_text: file.name.replace(/\.[^.]+$/, ""),
          },
        ]);

        if (!uploaded.publicly_reachable && uploaded.warning) {
          setNotice(uploaded.warning);
        }
      } catch (err) {
        setLocalError(
          err instanceof Error ? err.message : `We couldn't upload ${file.name}.`,
        );
      } finally {
        inFlight.current.delete(handle);
        setPending((p) => p.filter((item) => item.id !== id));
      }
    }
  }

  // Keep a ref so async uploads append to the latest list.
  const mediaRef = useRef(media);
  mediaRef.current = media;

  // ---- actions ------------------------------------------------------------
  const addUrl = () => {
    const url = urlDraft.trim();
    if (!/^https?:\/\//i.test(url)) {
      setLocalError("Enter a full URL starting with http:// or https://.");
      return;
    }
    if (mediaRef.current.some((m) => m.url === url)) {
      setLocalError("That link is already attached.");
      return;
    }
    if (mediaRef.current.length >= limit) {
      setLocalError(`This channel takes at most ${limit} attachment${limit === 1 ? "" : "s"}.`);
      return;
    }
    if (urlType === "image" && !acceptsImage) {
      setLocalError("This format does not accept images.");
      return;
    }
    if (urlType === "video" && !acceptsVideo) {
      setLocalError("This format does not accept videos.");
      return;
    }
    onChange([...mediaRef.current, { type: urlType, url }]);
    setUrlDraft("");
    setLocalError(null);
  };

  const remove = async (item: MediaItem) => {
    onChange(mediaRef.current.filter((m) => m.url !== item.url));
    if (item.asset_id) {
      try {
        await deleteMedia(item.asset_id);
      } catch {
        // The post is already detached. An orphaned file is harmless, but the
        // failure used to be swallowed with no trace at all, so it is noted.
        setNotice(
          "The attachment was removed from this post, but its stored file could not be " +
            "deleted. You can remove it from the media library later.",
        );
      }
    }
  };

  const combinedError = localError ?? error ?? null;

  return (
    <div className="media">
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) void handleFiles(e.target.files);
          e.target.value = "";
        }}
      />

      {full && limit !== Number.MAX_SAFE_INTEGER && (
        <p className="field__hint">
          This channel takes at most {limit} attachment{limit === 1 ? "" : "s"}.
        </p>
      )}

      {!full && (
        <>
          <button
            type="button"
            className={`dropzone ${dragging ? "is-dragging" : ""}`}
            disabled={disabled}
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (e.dataTransfer.files?.length) void handleFiles(e.dataTransfer.files);
            }}
          >
            <span className="dropzone__icon" aria-hidden="true">
              <IconUpload />
            </span>
            <span className="dropzone__title">
              Drop {acceptsImage && acceptsVideo ? "an image or video" : acceptsVideo ? "a video" : "an image"}{" "}
              here, or <span className="dropzone__link">browse</span>
            </span>
            <span className="dropzone__hint">
              {acceptLabel} · {ceiling}
            </span>
          </button>

          <div className="media__divider">
            <span>or use a link</span>
          </div>

          <div className="media__row">
            <select
              className="input media__type"
              value={urlType}
              disabled={disabled || (!acceptsImage && !acceptsVideo)}
              onChange={(e) => setUrlType(e.target.value as MediaType)}
              aria-label="Media type"
            >
              {acceptsImage && <option value="image">Image</option>}
              {acceptsVideo && <option value="video">Video</option>}
            </select>
            <input
              className="input"
              type="url"
              inputMode="url"
              placeholder="https://example.com/image.jpg"
              aria-label="Media URL"
              value={urlDraft}
              disabled={disabled}
              onChange={(e) => setUrlDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addUrl();
                }
              }}
            />
            <button
              type="button"
              className="btn btn--secondary btn--md"
              disabled={disabled || !urlDraft.trim()}
              onClick={addUrl}
            >
              Add
            </button>
          </div>
        </>
      )}

      {pending.length > 0 && (
        <ul className="media__list">
          {pending.map((p) => (
            <li key={p.id} className="media__item">
              <span className="media__thumb">
                {p.previewUrl ? (
                  <MediaThumb url={p.previewUrl} isVideo={p.isVideo} />
                ) : (
                  <IconVideo />
                )}
              </span>
              <span className="media__meta">
                <span className="media__name">{p.name}</span>
                <span className="media__type">
                  {p.percent < 100 ? `Uploading… ${p.percent}%` : "Finishing…"}
                </span>
              </span>
              <span className="media__progress" aria-hidden="true">
                <span className="media__progress-bar" style={{ width: `${p.percent}%` }} />
              </span>
            </li>
          ))}
        </ul>
      )}

      {media.length > 0 && (
        <ul className="media__list">
          {media.map((item, index) => (
            <li key={`${item.url}-${index}`} className="media__item">
              <span className="media__thumb">
                {item.type === "image" ? (
                  <img
                    src={item.preview_url || item.url}
                    alt=""
                    loading="lazy"
                    onError={(e) =>
                      e.currentTarget.parentElement?.classList.add("is-broken")
                    }
                  />
                ) : (
                  // A frame from the video rather than a generic icon, so an
                  // attached reel can be recognised at a glance.
                  <video
                    src={item.preview_url || item.url}
                    preload="metadata"
                    muted
                    playsInline
                  />
                )}
              </span>
              <span className="media__meta">
                <span className="media__name">
                  {item.filename || filename(item.url)}
                </span>
                <span className="media__type">
                  {item.type}
                  {item.asset_id ? " · uploaded" : " · linked"}
                </span>
              </span>
              <button
                type="button"
                className="media__remove"
                onClick={() => void remove(item)}
                disabled={disabled}
                aria-label={`Remove ${item.filename || filename(item.url)}`}
              >
                <IconX />
              </button>
            </li>
          ))}
        </ul>
      )}

      {notice && (
        <p className="media__notice" role="status">
          <IconInfo />
          <span>{notice}</span>
        </p>
      )}

      {requiresMedia && media.length === 0 && (
        <p className="field__hint">This format needs at least one image or video.</p>
      )}

      {combinedError && (
        <p className="field__error" role="alert">
          {combinedError}
        </p>
      )}
    </div>
  );
}

/**
 * Thumbnail for an in-flight upload.
 *
 * An image renders as an `<img>` and a video as a `<video>` that has fetched
 * just enough metadata to paint its first frame. `preload="metadata"` avoids
 * pulling an entire video into memory for a 40px thumbnail.
 */
function MediaThumb({ url, isVideo }: { url: string; isVideo: boolean }) {
  if (isVideo) {
    return <video src={url} preload="metadata" muted playsInline />;
  }
  return <img src={url} alt="" />;
}

function filename(url: string): string {
  try {
    const last = new URL(url, window.location.origin).pathname.split("/").filter(Boolean).pop();
    return last ?? url;
  } catch {
    return url;
  }
}

function formatBytes(bytes: number): string {
  // GB is not decorative: the video ceiling is 4 GB, and rendering that as
  // "4294967296 MB" is not a number anyone can act on.
  if (bytes >= 1024 * 1024 * 1024) {
    const gb = bytes / (1024 * 1024 * 1024);
    return `${Number.isInteger(gb) ? gb : gb.toFixed(1)} GB`;
  }
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} bytes`;
}

function IconUpload() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
    </svg>
  );
}

function IconX() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
    >
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

function IconVideo() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <rect x="2" y="5" width="14" height="14" rx="2" />
      <path d="m22 8-6 4 6 4V8Z" />
    </svg>
  );
}

function IconInfo() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </svg>
  );
}