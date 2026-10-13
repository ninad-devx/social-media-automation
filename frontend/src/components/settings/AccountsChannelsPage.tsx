/**
 * Settings > Accounts & Channels.
 *
 * Brands (accounts) and their channels, with the binding state that decides
 * whether each channel can actually publish.
 *
 * Two behaviours this screen previously did not have, both required by the audit:
 *
 * 1. Channels can be **removed**, not just disabled. Removal archives the row
 *    (history keeps resolving) and releases the Buffer profile so it can be
 *    imported into a different brand. It never touches Buffer.
 * 2. Channels can be **moved** to another brand, explicitly. The local id used
 *    to be derived from the Buffer id alone, which pinned every profile to one
 *    brand permanently.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CardHeader, EmptyState, Field } from "../../components/ui";
import { Select } from "../../components/ui/Select";
import { ChannelStatusBadge } from "../../components/settings/StatusBadge";
import { ApiError } from "../../services/api";
import { accountsApi } from "../../services/accounts";
import { channelsAdminApi, channelsApi } from "../../services/integrations";
import { integrationsApi } from "../../services/integrations";
import { useAllPlatforms } from "../../hooks/usePlatformCapability";
import type { Account } from "../../types/account";
import { channelCaption, type Channel } from "../../types/channel";
import type { Integration } from "../../types/integration";

export function AccountsChannelsPage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<{ tone: "success" | "danger"; message: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const platforms = useAllPlatforms();

  // manual binding form
  const [bindAccount, setBindAccount] = useState("");
  const [bindIntegration, setBindIntegration] = useState("");
  const [externalId, setExternalId] = useState("");
  const [platform, setPlatform] = useState("instagram");
  const [displayName, setDisplayName] = useState("");
  const [bindError, setBindError] = useState<string | null>(null);

  // reassign
  const [moveChannel, setMoveChannel] = useState<string | null>(null);
  const [moveTarget, setMoveTarget] = useState("");

  // new brand form
  const [newBrandName, setNewBrandName] = useState("");
  const [brandError, setBrandError] = useState<string | null>(null);
  const [savingBrand, setSavingBrand] = useState(false);

  const load = useCallback(async () => {
    try {
      // The administrative view, which is the only place archived and inactive
      // channels should be visible. Ordinary selectors use the default
      // endpoint, which hides them - that was the leak the audit found.
      const [accs, chans, ints] = await Promise.all([
        accountsApi.manage(),
        channelsApi.listAll(),
        integrationsApi.list(),
      ]);
      setAccounts(accs);
      setChannels(chans);
      setIntegrations(ints);
      setBindAccount((prev) => prev || accs[0]?.id || "");
      setBindIntegration((prev) => prev || ints[0]?.id || "");
    } catch (err) {
      setBanner({
        tone: "danger",
        message: err instanceof ApiError ? err.message : "Could not load accounts and channels.",
      });
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

  const connectedIntegrations = useMemo(
    () => integrations.filter((i) => i.status === "connected" && i.enabled),
    [integrations],
  );

  const platformOptions = useMemo(() => {
    const rows = platforms ?? [];
    if (rows.length === 0) {
      // Nothing hardcoded: until the capability list arrives the picker is
      // empty rather than offering a stale local copy of the platform table.
      return [];
    }
    return rows
      .filter((p) => p.known)
      .map((p) => ({ value: p.platform, label: p.label }));
  }, [platforms]);

  const run = useCallback(
    async (key: string, action: () => Promise<unknown>, success: string) => {
      setBusy(key);
      setBanner(null);
      try {
        await action();
        setBanner({ tone: "success", message: success });
      } catch (err) {
        setBanner({
          tone: "danger",
          message: err instanceof ApiError ? err.message : "That action failed.",
        });
      } finally {
        setBusy(null);
        await load();
      }
    },
    [load],
  );

  const toggleActive = (channel: Channel) =>
    run(
      `toggle:${channel.id}`,
      () => channelsAdminApi.update(channel.id, { active: !channel.active }),
      channel.active
        ? `"${channel.display_name}" is now disabled and hidden from every selector.`
        : `"${channel.display_name}" is enabled again.`,
    );

  const refresh = (channel: Channel) =>
    run(
      `refresh:${channel.id}`,
      () => channelsAdminApi.refresh(channel.id),
      `Refreshed "${channel.display_name}" from Buffer.`,
    );

  /** Archive, with an explicit confirmation that says what is and is not kept. */
  const remove = (channel: Channel) => {
    const ok = window.confirm(
      `Remove "${channel.display_name}" from ${accounts.find((a) => a.id === channel.account_id)?.name ?? "this brand"}?\n\n` +
        `This only affects this app:\n` +
        `• Nothing is deleted in Buffer. The profile, its posts and its queue are untouched.\n` +
        `• Its publishing history stays, so past posts remain visible.\n` +
        `• The Buffer profile is released, so you can import it into another brand.\n\n` +
        `You can restore it from this screen afterwards.`,
    );
    if (!ok) return;
    void run(
      `remove:${channel.id}`,
      () => channelsAdminApi.remove(channel.id, "removed_by_operator"),
      `Removed "${channel.display_name}". Nothing was changed in Buffer, and its history is intact.`,
    );
  };

  const restore = (channel: Channel) =>
    run(
      `restore:${channel.id}`,
      () => channelsAdminApi.restore(channel.id),
      `Restored "${channel.display_name}". Import the Buffer profile again to re-link it.`,
    );

  const reassign = (channel: Channel) => {
    const target = moveTarget;
    if (!target || target === channel.account_id) return;
    const targetName = accounts.find((a) => a.id === target)?.name ?? target;
    const ok = window.confirm(
      `Move "${channel.display_name}" to "${targetName}"?\n\n` +
        `Future publishing will use the new brand. Existing history keeps its original brand ` +
        `so past posts still read correctly.`,
    );
    if (!ok) return;
    setMoveChannel(null);
    void run(
      `move:${channel.id}`,
      () => channelsAdminApi.reassign(channel.id, target),
      `Moved "${channel.display_name}" to ${targetName}.`,
    );
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
      const created = await accountsApi.create({ name: newBrandName.trim(), type: "brand" });
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

  const toggleBrand = (account: Account) =>
    run(
      `brand:${account.id}`,
      () => accountsApi.update(account.id, { active: !account.active }),
      account.active
        ? `"${account.name}" is now disabled and hidden from the publishing selectors. Its history is untouched.`
        : `"${account.name}" enabled.`,
    );

  /**
   * Delete is only offered for a genuinely empty brand, and the backend refuses
   * anything with history. The message explains the safe alternative rather
   * than leaving the operator stuck.
   */
  const removeBrand = (account: Account) => {
    if (!window.confirm(`Delete the brand "${account.name}"? This cannot be undone.`)) return;
    setBusy(`brand:${account.id}`);
    setBanner(null);
    void (async () => {
      try {
        await accountsApi.remove(account.id);
        setBanner({ tone: "success", message: `Deleted "${account.name}".` });
      } catch (err) {
        const message =
          err instanceof ApiError ? err.message : "Could not delete the brand.";
        setBanner({
          tone: "danger",
          message: `${message} You can disable the brand instead - it disappears from every selector and the history is preserved.`,
        });
      } finally {
        setBusy(null);
        await load();
      }
    })();
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
    if (!bindIntegration) {
      setBindError(
        "Choose which Buffer integration to publish through. A channel cannot be linked without one.",
      );
      return;
    }
    try {
      await channelsAdminApi.bind({
        account_id: bindAccount,
        integration_id: bindIntegration,
        external_channel_id: externalId.trim(),
        platform,
        display_name: displayName.trim() || undefined,
      });
      setExternalId("");
      setDisplayName("");
      setBanner({
        tone: "success",
        message:
          "Channel linked and verified against Buffer. It is selectable in Quick Publish and AI Automation.",
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
          description="Use this only when automatic discovery is unavailable. The channel id is the value Buffer shows for that profile — it is never guessed. The integration is required, because that is what supplies the credential and workspace to publish with; the backend then verifies the profile really exists in that workspace."
        />
        <form className="settings__form" onSubmit={bind}>
          <Field label="Brand" htmlFor="bind-account">
            <Select
              aria-labelledby="bind-account-label"
              value={bindAccount}
              options={accounts.map((a) => ({ value: a.id, label: a.name }))}
              onChange={setBindAccount}
            />
          </Field>
          <Field label="Buffer integration" htmlFor="bind-integration">
            {connectedIntegrations.length === 0 ? (
              <p className="field__hint">
                No connected integration yet. Add one in Buffer Integrations first.
              </p>
            ) : (
              <Select
                aria-label="Buffer integration"
                value={bindIntegration}
                options={connectedIntegrations.map((i) => ({
                  value: i.id,
                  label: `${i.display_name}${i.external_organization_name ? ` · ${i.external_organization_name}` : ""}`,
                }))}
                onChange={setBindIntegration}
              />
            )}
          </Field>
          <Field label="Platform" htmlFor="bind-platform">
            <Select
              aria-label="Platform"
              value={platform}
              options={platformOptions}
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
            <Button type="submit" variant="primary" disabled={connectedIntegrations.length === 0}>
              Link channel
            </Button>
          </div>
        </form>
      </Card>

      {accounts.length === 0 && <EmptyState title="No brands yet" />}

      {accounts.map((account) => {
        const list = byAccount.get(account.id) ?? [];
        const live = list.filter((c) => c.active && !c.archived_at);
        const archived = list.filter((c) => c.archived_at);
        return (
          <Card key={account.id}>
            <CardHeader
              title={account.name}
              description={`${live.length} live channel${live.length === 1 ? "" : "s"} · ${
                live.filter((c) => c.publishable).length
              } publishable${archived.length ? ` · ${archived.length} removed` : ""}`}
              action={
                <div className="button-row" style={{ marginTop: 0 }}>
                  <Button
                    size="sm"
                    variant="ghost"
                    loading={busy === `brand:${account.id}`}
                    onClick={() => toggleBrand(account)}
                  >
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
                    <tr key={channel.id} style={channel.archived_at ? { opacity: 0.6 } : undefined}>
                      <td>
                        <strong>{channel.display_name}</strong>
                        {channel.username && <div className="muted small">@{channel.username}</div>}
                        {channel.publish_blocked_reason && (
                          <div className="muted small">{channel.publish_blocked_reason}</div>
                        )}
                      </td>
                      <td>{channel.platform}</td>
                      <td>{channel.integration_name ?? <span className="muted">Not linked</span>}</td>
                      <td>
                        <ChannelStatusBadge channel={channel} />
                      </td>
                      <td>
                        <Badge tone={channel.active ? "success" : "neutral"}>
                          {channel.active ? "Yes" : "No"}
                        </Badge>
                      </td>
                      <td className="cell-actions">
                        {moveChannel === channel.id ? (
                          <span className="inline-form">
                            <Select
                              aria-label={`Move ${channelCaption(channel)} to which brand`}
                              value={moveTarget}
                              options={accounts
                                .filter((a) => a.id !== channel.account_id)
                                .map((a) => ({ value: a.id, label: a.name }))}
                              onChange={setMoveTarget}
                            />
                            <Button size="sm" variant="primary" onClick={() => reassign(channel)}>
                              Move
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setMoveChannel(null)}>
                              Cancel
                            </Button>
                          </span>
                        ) : (
                          <>
                            {channel.integration_id && !channel.archived_at && (
                              <Button
                                size="sm"
                                variant="ghost"
                                loading={busy === `refresh:${channel.id}`}
                                onClick={() => refresh(channel)}
                              >
                                Refresh
                              </Button>
                            )}
                            {channel.archived_at ? (
                              <Button
                                size="sm"
                                variant="ghost"
                                loading={busy === `restore:${channel.id}`}
                                onClick={() => restore(channel)}
                              >
                                Restore
                              </Button>
                            ) : (
                              <>
                                {accounts.length > 1 && (
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => {
                                      setMoveChannel(channel.id);
                                      setMoveTarget(
                                        accounts.find((a) => a.id !== channel.account_id)?.id ?? "",
                                      );
                                    }}
                                  >
                                    Move
                                  </Button>
                                )}
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  loading={busy === `toggle:${channel.id}`}
                                  onClick={() => toggleActive(channel)}
                                >
                                  {channel.active ? "Disable" : "Enable"}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="danger"
                                  loading={busy === `remove:${channel.id}`}
                                  onClick={() => remove(channel)}
                                >
                                  Remove
                                </Button>
                              </>
                            )}
                          </>
                        )}
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