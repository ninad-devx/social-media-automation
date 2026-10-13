import { API_BASE } from "./api";

export interface UploadedMedia {
  asset_id: string;
  url: string;
  kind: "image" | "video";
  mime: string;
  size: number;
  filename: string;
  /** URL the browser uses for thumbnails. */
  preview_url?: string | null;
  /**
   * False when the URL is only reachable from this machine. Buffer runs in the
   * cloud and cannot download such a URL.
   */
  publicly_reachable: boolean;
  warning: string | null;
}

export interface UploadProgress {
  loaded: number;
  total: number;
  percent: number;
}

/**
 * The server's limit is the authority. This is only used to fail fast before a
 * pointless upload; the backend enforces it again and its message wins.
 */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

export class UploadError extends Error {
  readonly status: number | undefined;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "UploadError";
    this.status = status;
  }
}

export interface UploadHandle {
  promise: Promise<UploadedMedia>;
  cancel: () => void;
}

/**
 * Uploads a file to our backend with real progress events.
 *
 * The browser talks only to FastAPI. Buffer credentials and the n8n webhook
 * URL are never involved here.
 *
 * Cancellation and a timeout are now explicit. The previous version had neither,
 * so navigating away mid-upload left the request running and then wrote state
 * into an unmounted component, and a hung connection never resolved at all.
 */
export function uploadMediaWithHandle(
  file: File,
  onProgress?: (p: UploadProgress) => void,
  timeoutMs = 120_000,
): UploadHandle {
  let xhr: XMLHttpRequest | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const promise = new Promise<UploadedMedia>((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);

    const request = new XMLHttpRequest();
    xhr = request;
    request.open("POST", `${API_BASE}/api/media/upload`);
    // XMLHttpRequest defaults withCredentials to false, so without this the
    // session cookie is not sent and every upload 401s in production.
    request.withCredentials = true;

    const apiKey = import.meta.env.VITE_API_KEY;
    if (apiKey) request.setRequestHeader("X-API-Key", apiKey);

    const fail = (message: string, status?: number) => {
      if (timer) clearTimeout(timer);
      reject(new UploadError(message, status));
    };

    timer = setTimeout(() => {
      try {
        request.abort();
      } catch {
        /* already finished */
      }
      fail("The upload timed out. Please try again.");
    }, timeoutMs);

    request.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      onProgress?.({
        loaded: e.loaded,
        total: e.total,
        percent: Math.round((e.loaded / e.total) * 100),
      });
    };

    request.onload = () => {
      if (timer) clearTimeout(timer);

      const text = xhr?.responseText ?? "";
      let payload: unknown = null;
      try {
        payload = text ? JSON.parse(text) : null;
      } catch {
        // A 200 that is not JSON is the signature of a proxy or CDN serving an
        // interstitial page. Previously this resolved `null` and the caller then
        // threw a TypeError reading `.kind` of null, hiding the real cause.
        if (request.status >= 200 && request.status < 300) {
          fail(
            "The server returned an unexpected response instead of the uploaded file. " +
              "This usually means a proxy is serving a login or warning page.",
            request.status,
          );
          return;
        }
        fail(`The server rejected the upload (HTTP ${request.status}).`, request.status);
        return;
      }

      if (request.status >= 200 && request.status < 300) {
        if (!payload || typeof payload !== "object" || !("asset_id" in payload)) {
          fail("The upload response was incomplete. Please try again.", request.status);
          return;
        }
        resolve(payload as UploadedMedia);
        return;
      }

      const err = payload as { error?: { message?: string } } | null;
      fail(
        err?.error?.message ?? "We couldn't upload that file. Please try again.",
        request.status,
      );
    };

    request.onerror = () =>
      fail("We couldn't reach the server. Check your connection and try again.");
    request.onabort = () => fail("Upload cancelled.");

    request.send(form);
  });

  return {
    promise,
    cancel: () => {
      try {
        xhr?.abort();
      } catch {
        /* nothing to cancel */
      }
    },
  };
}

/** Convenience wrapper for callers that never cancel. */
export function uploadMedia(
  file: File,
  onProgress?: (p: UploadProgress) => void,
): Promise<UploadedMedia> {
  return uploadMediaWithHandle(file, onProgress).promise;
}

export function deleteMedia(assetId: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("DELETE", `${API_BASE}/api/media/${encodeURIComponent(assetId)}`);
    xhr.withCredentials = true;
    const apiKey = import.meta.env.VITE_API_KEY;
    if (apiKey) xhr.setRequestHeader("X-API-Key", apiKey);
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error("Delete failed")));
    xhr.onerror = () => reject(new Error("Delete failed"));
    xhr.send();
  });
}