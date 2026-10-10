import { useMemo } from "react";
import { useAccounts } from "../../hooks/useAccounts";
import { useChannels } from "../../hooks/useChannels";
import { Select, type SelectOption } from "../ui/Select";
import { PlatformAvatar } from "../ui/PlatformAvatar";
import { platformMeta } from "../../lib/platforms";
import type { Channel } from "../../types/channel";

/**
 * Account and channel selection.
 *
 * There is no per-account branching anywhere in this component. Channels are
 * fetched for whichever account is selected, so adding accounts and channels
 * is purely a backend configuration change.
 */
export function AccountChannelSelectors({
  accountId,
  channelId,
  onAccountChange,
  onChannelChange,
}: {
  accountId: string | null;
  channelId: string | null;
  onAccountChange: (id: string) => void;
  onChannelChange: (id: string) => void;
}) {
  const { data: accounts, loading: accountsLoading } = useAccounts();
  const { channels, loading: channelsLoading } = useChannels(accountId);

  const accountOptions = useMemo<SelectOption<string>[]>(
    () =>
      (accounts ?? []).map((a) => ({
        value: a.id,
        label: a.name,
        sublabel: `${a.channel_count} channel${a.channel_count === 1 ? "" : "s"}`,
        leading: <AccountMark name={a.name} color={a.color} />,
      })),
    [accounts],
  );

  const channelOptions = useMemo<SelectOption<string>[]>(
    () =>
      channels.map((c: Channel) => {
        const meta = platformMeta(c.platform);
        return {
          value: c.id,
          label: meta.label,
          // Surfaced so a channel with no working integration behind it is
          // obvious before the user writes a post, instead of failing on submit.
          sublabel: describeChannel(c),
          leading: <PlatformAvatar meta={meta} size={26} />,
        };
      }),
    [channels],
  );

  return (
    <div className="selector-grid">
      <div className="selector">
        <span className="selector__label" id="account-label">
          Account
        </span>
        <Select
          value={accountId}
          options={accountOptions}
          onChange={onAccountChange}
          placeholder={accountsLoading ? "Loading accounts…" : "Select an account"}
          disabled={accountsLoading || accountOptions.length === 0}
          aria-label="Account"
        />
      </div>

      <div className="selector">
        <span className="selector__label" id="channel-label">
          Channel
        </span>
        <Select
          value={channelId}
          options={channelOptions}
          onChange={onChannelChange}
          placeholder={
            !accountId
              ? "Select an account first"
              : channelsLoading
                ? "Loading channels…"
                : channels.length === 0
                  ? "No channels available"
                  : "Select a channel"
          }
          disabled={!accountId || channelsLoading || channelOptions.length === 0}
          aria-label="Channel"
        />
      </div>
    </div>
  );
}

/**
 * A one-line, honest description of what will happen if this channel is used.
 *
 * Channels predating integrations have `publishable === undefined` and are
 * treated as unknown-but-working, so the existing local setup looks exactly as
 * it did before this feature existed.
 */
function describeChannel(channel: Channel): string | undefined {
  const handle = channel.username ?? undefined;
  if (channel.is_locked) return "Locked in Buffer — cannot publish";
  if (channel.connection_status === "reauth_required") return "Disconnected in Buffer";
  if (channel.publishable === false) {
    return channel.integration_name ? `${channel.integration_name} needs attention` : "Not linked to a connection";
  }
  if (channel.queue_paused) return `${handle ? `${handle} · ` : ""}Queue paused`;
  return handle;
}

function AccountMark({ name, color }: { name: string; color: string | null }) {
  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();

  return (
    <span
      className="account-mark"
      style={color ? ({ "--am-color": color } as React.CSSProperties) : undefined}
      aria-hidden="true"
    >
      {initials || name[0]?.toUpperCase()}
    </span>
  );
}