import { useRef, useState } from "react";
import type { MediaItem, MediaType } from "../../types/publishing";
import { uploadMedia, deleteMedia, type UploadedMedia } from "../../services/media";

const ACCEPTED = "image/png,image/jpeg,image/webp,image/gif,video/mp4,video/quicktime,video/webm";
const MAX_BYTES = 25 * 1024 * 1024;

interface Pending {
  id: string;
  name: string;
  percent: number;
  previewUrl: string;
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
 * Buffer downloads media from a URL, so a locally uploaded file needs a
 * publicly reachable address before it can actually be published. The backend
 * tells us whether the returned URL qualifies, and we surface that here
 * rather than letting the publish fail silently.
 */
export function MediaPicker({
  media,
  onChange,
  disabled = false,
  error,
  requiresMedia = false,
  maxItems = 10,
}: {
  media: MediaItem[];
  onChange: (media: MediaItem[]) => void;
  disabled?: boolean;
  error?: string | null;
  requiresMedia?: boolean;
  maxItems?: number;
}) {
  const [pending, setPending] = useState<Pending[]>([]);
  const [urlDraft, setUrlDraft] = useState("");
  const [urlType, setUrlType] = useState<MediaType>("image");
  const [notice, setNotice] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const full = media.length >= maxItems;

  // ---- uploads ------------------------------------------------------------
  async function handleFiles(files: FileList | File[]) {
    setLocalError(null);
    setNotice(null);

    const room = maxItems - media.length;
    if (room <= 0) return;

    for (const file of Array.from(files).slice(0, room)) {
      if (file.size > MAX_BYTES) {
        setLocalError(`${file.name} is larger than 25 MB.`);
        continue;
      }
      if (!file.type.startsWith("image/") && !file.type.startsWith("video/")) {
        setLocalError(`${file.name} isn't a supported image or video.`);
        continue;
      }

      const id = `${file.name}-${file.size}-${file.lastModified}`;
      const previewUrl = file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : "";

      setPending((p) => [...p, { id, name: file.name, percent: 0, previewUrl }]);

      try {
        const uploaded: UploadedMedia = await uploadMedia(file, (progress) => {
          setPending((p) =>
            p.map((item) => (item.id === id ? { ...item, percent: progress.percent } : item)),
          );
        });

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
        /* the post is already detached; a stale file is harmless */
      }
    }
  };

  const combinedError = localError ?? error ?? null;

  return (
    <div className="media">
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) void handleFiles(e.target.files);
          e.target.value = "";
        }}
      />

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
              Drop an image or video here, or <span className="dropzone__link">browse</span>
            </span>
            <span className="dropzone__hint">PNG, JPG, GIF, WEBP, MP4 · up to 25 MB</span>
          </button>

          <div className="media__divider">
            <span>or use a link</span>
          </div>

          <div className="media__row">
            <select
              className="input media__type"
              value={urlType}
              disabled={disabled}
              onChange={(e) => setUrlType(e.target.value as MediaType)}
              aria-label="Media type"
            >
              <option value="image">Image</option>
              <option value="video">Video</option>
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
                {p.previewUrl ? <img src={p.previewUrl} alt="" /> : <IconVideo />}
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
          {media.map((item) => (
            <li key={item.url} className="media__item">
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
                  <IconVideo />
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
        <p className="field__hint">This channel needs at least one image or video.</p>
      )}

      {combinedError && (
        <p className="field__error" role="alert">
          {combinedError}
        </p>
      )}
    </div>
  );
}

function filename(url: string): string {
  try {
    const last = new URL(url, window.location.origin).pathname.split("/").filter(Boolean).pop();
    return last ?? url;
  } catch {
    return url;
  }
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