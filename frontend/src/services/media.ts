import { API_BASE, api } from "./api";

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

export interface UploadLimits {
  image_max_bytes: number;
  video_max_bytes: number;
  chunk_threshold_bytes: number;
  chunk_bytes: number;
}

interface ChunkedSession {
  upload_id: string;
  filename: string;
  size: number;
  chunk_size: number;
  chunk_count: number;
  received: number[];
  received_bytes: number;
  complete: boolean;
}

/**
 * Used only until the server answers `/api/media/upload-limits`.
 *
 * These used to be the hardcoded 25 MB that rejected every Facebook Reel over
 * three minutes long. They match the backend defaults so a failed limits fetch
 * degrades to the old behaviour rather than to no uploads at all.
 */
export const FALLBACK_UPLOAD_LIMITS: UploadLimits = {
  image_max_bytes: 25 * 1024 * 1024,
  video_max_bytes: 4 * 1024 * 1024 * 1024,
  chunk_threshold_bytes: 25 * 1024 * 1024,
  chunk_bytes: 8 * 1024 * 1024,
};

/** Fetch the ceilings this deployment enforces. */
export function fetchUploadLimits(signal?: AbortSignal): Promise<UploadLimits> {
  return api.get<UploadLimits>("/api/media/upload-limits", signal);
}

/**
 * The limit that applies to one file.
 *
 * A file is validated by what it turns out to be, not by its extension, so the
 * image ceiling and the video ceiling differ. Refusing a 30 MB MP4 here would
 * reproduce exactly the bug this replaced.
 */
export function limitForFile(file: File, limits: UploadLimits = FALLBACK_UPLOAD_LIMITS): number {
  return file.type.startsWith("video/") ? limits.video_max_bytes : limits.image_max_bytes;
}

/** True when the file has to travel as chunks rather than one request. */
export function needsChunking(file: File, limits: UploadLimits = FALLBACK_UPLOAD_LIMITS): boolean {
  return file.size > limits.chunk_threshold_bytes;
}

export class UploadError extends Error {
  readonly status: number | undefined;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "UploadError";
    this.status = status;
  }
}

export class UploadCancelledError extends UploadError {
  constructor() {
    super("Upload cancelled.");
    this.name = "UploadCancelledError";
  }
}

export interface UploadHandle {
  promise: Promise<UploadedMedia>;
  cancel: () => void;
}

function apiKeyHeader(): Record<string, string> {
  const apiKey = import.meta.env.VITE_API_KEY;
  return apiKey ? { "X-API-Key": apiKey } : {};
}

/**
 * Uploads a file to our backend with real progress events.
 *
 * Two protocols behind one handle:
 *
 *   * **Single request** - the whole file as multipart on `/api/media/upload`.
 *     Right for images, and anything small enough to survive a proxy.
 *   * **Chunked** - a session, fixed-size chunks, then `complete`. Right for
 *     the multi-gigabyte video Facebook accepts. A 4 GB body in one request
 *     does not get through the reverse proxy, and a browser gives up on it long
 *     before that; a failed chunk costs one chunk instead of the whole file.
 *
 * The browser talks only to FastAPI. Buffer credentials and the n8n webhook
 * URL are never involved here.
 */
export function uploadMediaWithHandle(
  file: File,
  onProgress?: (p: UploadProgress) => void,
  limits: UploadLimits = FALLBACK_UPLOAD_LIMITS,
): UploadHandle {
  return needsChunking(file, limits)
    ? chunkedUpload(file, onProgress, limits)
    : singleUpload(file, onProgress);
}

function singleUpload(
  file: File,
  onProgress?: (p: UploadProgress) => void,
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

    for (const [key, value] of Object.entries(apiKeyHeader())) {
      request.setRequestHeader(key, value);
    }

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
    }, UPLOAD_TIMEOUT_MS);

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

/** Per-chunk timeout. Generous, because a slow chunk is still progressing. */
const UPLOAD_TIMEOUT_MS = 120_000;
const CHUNK_TIMEOUT_MS = 300_000;
/** A chunk is retried this many times before the whole upload fails. */
const CHUNK_ATTEMPTS = 3;
/** Grows per attempt: a busy server needs longer than a flaky connection. */
const RETRY_BACKOFF_MS = 1_500;

function chunkedUpload(
  file: File,
  onProgress: ((p: UploadProgress) => void) | undefined,
  limits: UploadLimits,
): UploadHandle {
  let cancelled = false;
  let current: XMLHttpRequest | null = null;
  let sessionId: string | null = null;

  const promise = (async () => {
    const session = await api.post<ChunkedSession>("/api/media/uploads", {
      filename: file.name,
      size: file.size,
    });
    sessionId = session.upload_id;

    const chunkSize = session.chunk_size || limits.chunk_bytes;
    const total = session.size || file.size;
    // Bytes confirmed by the server, not by the browser. The difference is what
    // is in flight, and reporting the browser's view would jump backwards every
    // time a chunk had to be retried.
    let confirmed = 0;

    const report = (inFlight: number) => {
      const loaded = Math.min(total, confirmed + inFlight);
      onProgress?.({
        loaded,
        total,
        percent: total > 0 ? Math.min(99, Math.round((loaded / total) * 100)) : 0,
      });
    };

    for (let index = 0; index < session.chunk_count; index += 1) {
      throwIfCancelled();
      const start = index * chunkSize;
      const end = Math.min(total, start + chunkSize);
      const block = file.slice(start, end);

      await sendChunk(index, block, (inFlight) => report(inFlight));
      confirmed = end;
      report(0);
    }

    report(0);
    const done = await api.post<UploadedMedia>(`/api/media/uploads/${session.upload_id}/complete`);
    sessionId = null;

    onProgress?.({ loaded: total, total, percent: 100 });
    return done;
  })();

  return {
    promise,
    cancel: () => {
      cancelled = true;
      try {
        current?.abort();
      } catch {
        /* nothing to cancel */
      }
      // Drop the partial upload server-side. Without this a cancelled 4 GB
      // video would sit in staging until the TTL sweep ran.
      if (sessionId) {
        const id = sessionId;
        sessionId = null;
        void api.del(`/api/media/uploads/${id}`).catch(() => undefined);
      }
    },
  };

  function throwIfCancelled() {
    if (cancelled) throw new UploadCancelledError();
  }

  async function sendChunk(
    index: number,
    block: Blob,
    onInflight: (bytes: number) => void,
  ): Promise<void> {
    let lastError: Error | null = null;

    for (let attempt = 1; attempt <= CHUNK_ATTEMPTS; attempt += 1) {
      throwIfCancelled();
      try {
        await putChunk(index, block, onInflight);
        return;
      } catch (err) {
        if (err instanceof UploadCancelledError) throw err;
        lastError = err as Error;
        if (attempt < CHUNK_ATTEMPTS) {
          await delay(RETRY_BACKOFF_MS * attempt);
        }
      }
    }

    throw (
      lastError ??
      new UploadError(`Part ${index + 1} could not be uploaded. Please try again.`)
    );
  }

  function putChunk(index: number, block: Blob, onInflight: (bytes: number) => void): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const request = new XMLHttpRequest();
      current = request;
      request.open(
        "PUT",
        `${API_BASE}/api/media/uploads/${sessionId}/chunks/${index}`,
      );
      request.withCredentials = true;
      for (const [key, value] of Object.entries(apiKeyHeader())) {
        request.setRequestHeader(key, value);
      }
      request.setRequestHeader("Content-Type", "application/octet-stream");

      let timer: ReturnType<typeof setTimeout> | null = null;
      const finish = () => {
        if (timer) clearTimeout(timer);
        current = null;
      };
      const fail = (message: string, status?: number) => {
        finish();
        reject(new UploadError(message, status));
      };

      timer = setTimeout(() => {
        try {
          request.abort();
        } catch {
          /* already finished */
        }
        fail("That part of the upload stopped responding. Please try again.");
      }, CHUNK_TIMEOUT_MS);

      request.upload.onprogress = (e) => {
        if (e.lengthComputable) onInflight(e.loaded);
      };

      request.onload = () => {
        if (request.status >= 200 && request.status < 300) {
          finish();
          resolve();
          return;
        }
        let message = `The server rejected part ${index + 1} (HTTP ${request.status}).`;
        try {
          const parsed = JSON.parse(request.responseText ?? "");
          if (parsed?.error?.message) message = parsed.error.message;
        } catch {
          /* keep the generic message */
        }
        fail(message, request.status);
      };

      request.onerror = () => fail("We lost the connection mid-upload. Please try again.");
      request.onabort = () =>
        fail(cancelled ? "Upload cancelled." : "That part of the upload was interrupted.");

      request.send(block);
    });
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Convenience wrapper for callers that never cancel. */
export function uploadMedia(
  file: File,
  onProgress?: (p: UploadProgress) => void,
  limits?: UploadLimits,
): Promise<UploadedMedia> {
  return uploadMediaWithHandle(file, onProgress, limits).promise;
}

export function deleteMedia(assetId: string): Promise<void> {
  return api.del<void>(`/api/media/${encodeURIComponent(assetId)}`);
}
