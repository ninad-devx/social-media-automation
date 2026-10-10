export interface Channel {
  id: string;
  account_id: string;
  platform: string;
  display_name: string;
  username: string | null;
  avatar: string | null;
  description: string | null;
  active: boolean;
  /**
   * Integration binding. All optional so this stays compatible with channels
   * created before integrations existed.
   */
  integration_id?: string | null;
  integration_name?: string | null;
  connection_status?: string;
  is_locked?: boolean;
  queue_paused?: boolean;
  capabilities?: string[];
  /** False when no working integration sits behind this channel. */
  publishable?: boolean;
  last_synced_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}
