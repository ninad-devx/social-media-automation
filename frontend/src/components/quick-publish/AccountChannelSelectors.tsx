import { useMemo } from "react";
import { useAccounts } from "../../hooks/useAccounts";
import { useChannels } from "../../hooks/useChannels";
import { Select, type SelectOption } from "../ui/Select";
import { PlatformAvatar } from "../ui/PlatformAvatar";
import { platformMeta } from "../../lib/platforms";
import { channelAccountName, channelStatus, type Channel } from "../../types/channel";

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
        const status = channelStatus(c);
        return {
          value: c.id,
          label: meta.label,
          // The backend's verdict, with its reason. Previously only a caption
          // changed and every channel stayed selectable, so a locked or
          // disconnected profile looked fine right up to a failed publish.
          sublabel: [
            // The owning brand, always. Channels here are already filtered by
            // the brand above, but the name is kept next to the profile so the
            // operator can confirm what they are publishing to without
            // cross-referencing two dropdowns.
            channelAccountName(c),
            c.username,
            status.publishableHint ?? status.label,
          ]
            .filter(Boolean)
            .join(" · "),
          leading: <PlatformAvatar meta={meta} size={26} />,
        };
      }),
    [channels],
  );

  const selected = channels.find((c) => c.id === channelId) ?? null;
  const selectedStatus = selected ? channelStatus(selected) : null;

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
          aria-labelledby="channel-label"
        />
        {selectedStatus && selectedStatus.detail ? (
          <p className={`field__hint ${selectedStatus.tone === "success" ? "" : "field__hint--warn"}`}>
            {selectedStatus.detail}
          </p>
        ) : null}
      </div>
    </div>
  );
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