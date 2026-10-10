/**
 * Settings > Accounts & Channels.
 *
 * Brands (accounts) and their channels, with the binding state that decides
 * whether each channel can actually publish. Everything here is data-driven:
 * adding a channel through Buffer Integrations makes it appear here and in
 * every selector without a code change.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CardHeader, EmptyState, Field } from "../../components/ui";
import { Select } from "../../components/ui/Select";
import { ChannelStatusBadge } from "../../components/settings/StatusBadge";
import { ApiError } from "../../services/api";
import { accountsApi } from "../../services/accounts";
import { channelsAdminApi } from "../../services/integrations";
import type { Account } from "../../types/account";
import type { Channel } from "../../types/channel";

const PLATFORMS = [
  "instagram",
  "facebook",
  "linkedin",
  "twitter",
  "threads",
  "tiktok",
  "mastodon",
];

export function AccountsChannelsPage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<{ tone: "success" | "danger"; message: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // manual binding form
  const [bindAccount, setBindAccount] = useState("");
  const [externalId, setExternalId] = useState("");
  const [platform, setPlatform] = useState("instagram");
  const [displayName, setDisplayName] = useState("");
  const [bindError, setBindError] = useState<string | null>(null);

  // new brand form
  const [newBrandName, setNewBrandName] = useState("");
  const [brandError, setBrandError] = useState<string | null>(null);
  const [savingBrand, setSavingBrand] = useState(false);

  const load = useCallback(async () => {
    try {
      // `manage` returns empty and inactive brands too, so a brand created a
      // moment ago is visible immediately instead of only once it has channels.
      const [accs, chans] = await Promise.all([accountsApi.manage(), accountsApi.allChannels()]);
      setAccounts(accs);
      setChannels(chans);
      setBindAccount((prev) => prev || accs[0]?.id || "");
    } catch {
      setBanner({ tone: "danger", message: "Could not load accounts and channels." });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const byAccount = useMemo(() => {
    const map = new Map<string, Channel[]>();
    for (const channel of channels) {
      const list = map.get(channel.account_id) ?? [];
      list.push(channel);
      map.set(channel.account_id, list);
    }
    return map;
  }, [channels]);

  const toggleActive = async (channel: Channel) => {
    setBusy(channel.id);
    setBanner(null);
    try {
      await channelsAdminApi.update(channel.id, { active: !channel.active });
      setBanner({
        tone: "success",
        message: channel.active
          ? `"${channel.display_name}" disabled. It is hidden from every selector but nothing was deleted.`
          : `"${channel.display_name}" enabled and available again.`,
      });
      await load();
    } catch (err) {
      setBanner({ tone: "danger", message: err instanceof ApiError ? err.message : "Update failed." });
    } finally {
      setBusy(null);
    }
  };

  const refresh = async (channel: Channel) => {
    setBusy(channel.id);
    setBanner(null);
    try {
      await channelsAdminApi.refresh(channel.id);
      setBanner({ tone: "success", message: `Refreshed "${channel.display_name}" from Buffer.` });
      await load();
    } catch (err) {
      setBanner({
        tone: "danger",
        message: err instanceof ApiError ? err.message : "Refresh failed.",
      });
    } finally {
      setBusy(null);
    }
  };

  const createBrand = async (event: React.FormEvent) => {
    event.preventDefault();
    setBrandError(null);
    if (!newBrandName.trim()) {
      setBrandError("Enter a name for the brand.");
      return;
    }
    setSavingBrand(true);
    try {
      const created = await accountsApi.create({
        name: newBrandName.trim(),
        type: "brand",
      });
      setNewBrandName("");
      setBindAccount((prev) => prev || created.id);
      setBanner({
        tone: "success",
        message: `"${created.name}" created. Import channels into it from Buffer Integrations.`,
      });
      await load();
    } catch (err) {
      setBrandError(err instanceof ApiError ? err.message : "Could not create the brand.");
    } finally {
      setSavingBrand(false);
    }
  };

  const toggleBrand = async (account: Account) => {
    try {
      await accountsApi.update(account.id, { active: !account.active });
      setBanner({
        tone: "success",
        message: account.active
          ? `"${account.name}" disabled. It no longer appears in the publishing selectors.`
          : `"${account.name}" enabled.`,
      });
      await load();
    } catch (err) {
      setBanner({ tone: "danger", message: err instanceof ApiError ? err.message : "Update failed." });
    }
  };

  const removeBrand = async (account: Account) => {
    if (!window.confirm(`Delete the brand "${account.name}"? This cannot be undone.`)) return;
    try {
      await accountsApi.remove(account.id);
      setBanner({ tone: "success", message: `Deleted "${account.name}".` });
      await load();
    } catch (err) {
      setBanner({
        tone: "danger",
        message: err instanceof ApiError ? err.message : "Could not delete the brand.",
      });
    }
  };

  const bind = async (event: React.FormEvent) => {
    event.preventDefault();
    setBindError(null);
    if (!externalId.trim()) {
      setBindError("Enter the channel id that Buffer shows for this profile.");
      return;
    }
    if (!bindAccount) {
      setBindError("Choose the brand this channel belongs to.");
      return;
    }
    try {
      await channelsAdminApi.bind({
        account_id: bindAccount,
        external_channel_id: externalId.trim(),
        platform,
        display_name: displayName.trim() || undefined,
      });
      setExternalId("");
      setDisplayName("");
      setBanner({
        tone: "success",
        message: "Channel linked. It is now selectable in Quick Publisher and AI Automation.",
      });
      await load();
    } catch (err) {
      setBindError(err instanceof ApiError ? err.message : "Could not link the channel.");
    }
  };

  if (loading) return <p className="muted">Loading accounts and channels…</p>;

  return (
    <div className="stack">
      {banner && (
        <div className={`banner banner--${banner.tone}`} role="status">
          {banner.message}
        </div>
      )}

      <Card>
        <CardHeader
          title="Create a brand"
          description="Brands are data, not code. Create one here, then import channels into it from Buffer Integrations."
        />
        <form className="settings__form" onSubmit={createBrand}>
          <Field label="Brand name" htmlFor="brand-name" error={brandError}>
            <input
              id="brand-name"
              className="input"
              value={newBrandName}
              onChange={(e) => setNewBrandName(e.target.value)}
              placeholder="Glaamora"
              maxLength={160}
            />
          </Field>
          <div>
            <Button type="submit" variant="primary" loading={savingBrand}>
              Create brand
            </Button>
          </div>
        </form>
      </Card>

      <Card>
        <CardHeader
          title="Link a channel manually"
          description="Use this only when automatic discovery is unavailable or you want to attach a single profile by hand. The channel id is the value Buffer shows for that profile — it is never guessed from the display name."
        />
        <form className="settings__form" onSubmit={bind}>
          <Field label="Brand" htmlFor="bind-account">
            <Select
              aria-label="Brand"
              value={bindAccount}
              options={accounts.map((a) => ({ value: a.id, label: a.name }))}
              onChange={setBindAccount}
            />
          </Field>
          <Field label="Platform" htmlFor="bind-platform">
            <Select
              aria-label="Platform"
              value={platform}
              options={PLATFORMS.map((p) => ({ value: p, label: p }))}
              onChange={setPlatform}
            />
          </Field>
          <Field
            label="Buffer channel id"
            htmlFor="bind-external"
            error={bindError}
            hint="Buffer calls these channels; older docs call them profiles."
          >
            <input
              id="bind-external"
              className="input"
              value={externalId}
              onChange={(e) => setExternalId(e.target.value)}
              placeholder="6ac7b5056a5c39ccb6524992"
            />
          </Field>
          <Field label="Display name" htmlFor="bind-name" optional>
            <input
              id="bind-name"
              className="input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Glaamora"
            />
          </Field>
          <div>
            <Button type="submit" variant="primary">
              Link channel
            </Button>
          </div>
        </form>
      </Card>

      {accounts.length === 0 && <EmptyState title="No brands yet" />}

      {accounts.map((account) => {
        const list = byAccount.get(account.id) ?? [];
        return (
          <Card key={account.id}>
            <CardHeader
              title={account.name}
              description={`${list.length} channel(s) · ${list.filter((c) => c.active && c.publishable !== false).length} publishable`}
              action={
                <div className="button-row" style={{ marginTop: 0 }}>
                  <Button size="sm" variant="ghost" onClick={() => toggleBrand(account)}>
                    {account.active ? "Disable" : "Enable"}
                  </Button>
                  {list.length === 0 && (
                    <Button size="sm" variant="danger" onClick={() => removeBrand(account)}>
                      Delete
                    </Button>
                  )}
                  <Badge tone={account.active ? "success" : "neutral"}>
                    {account.active ? "Active" : "Inactive"}
                  </Badge>
                </div>
              }
            />

            {list.length === 0 ? (
              <p className="muted">
                No channels yet. Add them from <strong>Buffer Integrations</strong> by discovering this
                brand&apos;s channels, or link one manually above.
              </p>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Channel</th>
                    <th>Platform</th>
                    <th>Integration</th>
                    <th>Status</th>
                    <th>Enabled</th>
                    <th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {list.map((channel) => (
                    <tr key={channel.id}>
                      <td>
                        <strong>{channel.display_name}</strong>
                        {channel.username && <div className="muted small">@{channel.username}</div>}
                      </td>
                      <td>{channel.platform}</td>
                      <td>
                        {channel.integration_name ?? <span className="muted">Not linked</span>}
                      </td>
                      <td>
                        <div className="cell-stack">
                          <ChannelStatusBadge
                            connectionStatus={channel.connection_status}
                            locked={channel.is_locked}
                            queuePaused={channel.queue_paused}
                          />
                          {channel.publishable === false && channel.active && (
                            <Badge tone="warning">Will not publish</Badge>
                          )}
                        </div>
                      </td>
                      <td>
                        <Badge tone={channel.active ? "success" : "neutral"}>
                          {channel.active ? "Yes" : "No"}
                        </Badge>
                      </td>
                      <td className="cell-actions">
                        {channel.integration_id && (
                          <Button size="sm" variant="ghost" onClick={() => refresh(channel)} loading={busy === channel.id}>
                            Refresh
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" onClick={() => toggleActive(channel)} loading={busy === channel.id}>
                          {channel.active ? "Disable" : "Enable"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        );
      })}
    </div>
  );
}
