/**
 * Channel types.
 *
 * `publishable` is the backend's authoritative verdict and
 * `publish_blocked_reason` says why when it is false. The audit found the UI
 * computing its own idea of a channel's health in three components and never
 * reading these fields at all, so a locked or disconnected channel stayed fully
 * selectable. Both fields now travel together and are rendered everywhere a
 * channel appears.
 */

export interface Channel {
  id: string;
  account_id: string;
  /**
   * The owning brand's name, as returned by the backend.
   *
   * The backend populated this field only on some responses, so a picker that
   * listed channels from more than one brand could only show bare profile
   * names and the operator had no way to tell which brand a channel belonged
   * to before assigning it. Every picker that can span brands renders it.
   */
  account_name?: string | null;
  platform: string;
  display_name: string;
  username: string | null;
  avatar: string | null;
  description: string | null;
  active: boolean;
  archived_at: string | null;
  archived_reason: string | null;
  integration_id: string | null;
  integration_name: string | null;
  connection_status: "connected" | "disconnected" | "reauth_required" | "locked" | "unknown";
  is_locked: boolean;
  queue_paused: boolean;
  capabilities: string[];
  /** The backend's verdict. Never to be re-derived in the UI. */
  publishable: boolean;
  publish_blocked_reason: string | null;
  publish_blocked_code: string | null;
  last_synced_at: string | null;
}

/**
 * The brand a channel belongs to, for display.
 *
 * Falls back to the raw id so a channel whose brand name did not come back is
 * still attributable rather than appearing to belong to nobody.
 */
export function channelAccountName(channel: Pick<Channel, "account_id" | "account_name">): string {
  return channel.account_name?.trim() || channel.account_id;
}

/**
 * A single-line "brand - profile" caption.
 *
 * Every control that offers a channel from more than one brand uses this, so
 * the brand is always visible next to the profile and assigning a channel to a
 * brand is never a guess.
 */
export function channelCaption(
  channel: Pick<Channel, "id" | "display_name" | "username" | "account_id" | "account_name">,
): string {
  const profile = channel.display_name?.trim() || channel.username?.trim() || channel.id;
  return `${channelAccountName(channel)} - ${profile}`;
}

/**
 * One label for a channel's health, derived from the backend fields.
 *
 * Returns a tone as well as a label so nothing can render a green "Connected"
 * the backend has not actually verified - which is exactly what the audit found
 * AccountsPage doing for every channel regardless of state.
 */
export function channelStatus(
  channel: Pick<
    Channel,
    | "active"
    | "archived_at"
    | "publishable"
    | "publish_blocked_reason"
    | "connection_status"
    | "is_locked"
    | "queue_paused"
  >,
): {
  label: string;
  tone: "success" | "warning" | "danger" | "neutral";
  detail: string | null;
  /** Short form for dense captions, e.g. a select sublabel. */
  publishableHint?: string | null;
} {
  if (channel.archived_at) {
    return {
      label: "Removed",
      tone: "neutral",
      detail: channel.publish_blocked_reason ?? "This channel was removed from the app.",
      publishableHint: "Removed",
    };
  }
  if (channel.is_locked) {
    return {
      label: "Locked",
      tone: "danger",
      detail: "Buffer has this channel locked, so it cannot receive posts.",
      publishableHint: "Locked — cannot publish",
    };
  }
  if (channel.connection_status === "reauth_required") {
    return {
      label: "Disconnected",
      tone: "danger",
      detail: "Buffer reports this profile as disconnected. Reconnect it in Buffer.",
      publishableHint: "Disconnected in Buffer",
    };
  }
  if (!channel.active) {
    return {
      label: "Disabled",
      tone: "neutral",
      detail: "Turned off in Settings.",
      publishableHint: "Disabled",
    };
  }
  if (!channel.publishable) {
    return {
      label: "Not linked",
      tone: "warning",
      detail: channel.publish_blocked_reason,
      publishableHint: channel.publish_blocked_reason ?? "Not linked",
    };
  }
  if (channel.queue_paused) {
    return {
      label: "Queue paused",
      tone: "warning",
      detail:
        channel.publish_blocked_reason ??
        "Buffer's queue for this channel is paused, so posts will be held.",
      publishableHint: "Queue paused",
    };
  }
  if (channel.connection_status === "unknown") {
    return {
      label: "Not checked",
      tone: "neutral",
      detail: "This channel has never been checked against Buffer.",
      publishableHint: "Not checked",
    };
  }
  return { label: "Connected", tone: "success", detail: null, publishableHint: null };
}