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
 * Uploads a file to our backend with real progress events.
 *
 * The browser talks only to FastAPI. Buffer credentials and the n8n webhook
 * URL are never involved here.
 */
export function uploadMedia(
  file: File,
  onProgress?: (p: UploadProgress) => void,
): Promise<UploadedMedia> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/media/upload`);

    const apiKey = import.meta.env.VITE_API_KEY;
    if (apiKey) xhr.setRequestHeader("X-API-Key", apiKey);

    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      onProgress?.({
        loaded: e.loaded,
        total: e.total,
        percent: Math.round((e.loaded / e.total) * 100),
      });
    };

    xhr.onload = () => {
      let payload: unknown = null;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch {
        payload = null;
      }

      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as UploadedMedia);
        return;
      }

      const err = payload as { error?: { message?: string } } | null;
      reject(new Error(err?.error?.message ?? "We couldn't upload that file. Please try again."));
    };

    xhr.onerror = () =>
      reject(new Error("We couldn't reach the server. Check your connection and try again."));
    xhr.onabort = () => reject(new Error("Upload cancelled."));

    xhr.send(form);
  });
}

export function deleteMedia(assetId: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("DELETE", `${API_BASE}/api/media/${encodeURIComponent(assetId)}`);
    const apiKey = import.meta.env.VITE_API_KEY;
    if (apiKey) xhr.setRequestHeader("X-API-Key", apiKey);
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error("Delete failed")));
    xhr.onerror = () => reject(new Error("Delete failed"));
    xhr.send();
  });
}