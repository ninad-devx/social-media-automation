export type PublishMode = "NOW" | "SCHEDULE";
export type MediaType = "image" | "video";

export interface MediaItem {
  type: MediaType;
  url: string;
  alt_text?: string | null;
  /** Set when the file came from our own upload endpoint. */
  asset_id?: string | null;
  /** Original file name, so the UI doesn't have to show the storage id. */
  filename?: string | null;
  /**
   * URL the browser uses for thumbnails. Separate from `url` because a public
   * tunnel may serve an interstitial page to browsers while serving the real
   * file to Buffer. Falls back to `url`.
   */
  preview_url?: string | null;
}

export interface PublishRequest {
  account_id: string;
  channel_id: string;
  content: string;
  media: MediaItem[];
  publish_mode: PublishMode;
  scheduled_at: string | null;
  timezone: string;
}

/** Successful publish / schedule result as returned by our own backend. */
export interface PublishResponse {
  success: true;
  request_id: string;
  status: "published" | "scheduled";
  account_id: string;
  channel_id: string;
  platform: string;
  account_name: string | null;
  channel_name: string | null;
  buffer_publication_id: string | null;
  external_link: string | null;
  scheduled_at: string | null;
  timezone: string | null;
  content_length: number;
  media_count: number;
  idempotent_replay: boolean;
  n8n_request_id: string | null;
}

/** One normalized error shape. The backend guarantees there is only one. */
export interface ApiErrorBody {
  success: false;
  request_id: string;
  error: { code: string; message: string; field?: string | null };
  errors?: Array<{ code: string; message: string; field?: string | null }>;
}

export interface PublishLogEntry {
  id: number;
  request_id: string;
  account_id: string;
  channel_id: string;
  platform: string;
  content_preview: string;
  media_count: number;
  publish_mode: string;
  scheduled_at: string | null;
  timezone: string;
  status: string;
  buffer_publication_id: string | null;
  external_link: string | null;
  error_code: string | null;
  created_at: string | null;
}

export interface PublishLogPage {
  items: PublishLogEntry[];
  total: number;
  limit: number;
  offset: number;
}