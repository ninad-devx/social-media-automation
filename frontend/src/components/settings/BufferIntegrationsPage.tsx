/**
 * Settings > Buffer Integrations.
 *
 * The whole point of this page: adding a Buffer account or workspace is a form
 * submission, not a code change and not an n8n edit. Nothing here writes a
 * credential anywhere but the backend, and no stored secret is ever displayed.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CardHeader, EmptyState, Field } from "../../components/ui";
import { Select } from "../../components/ui/Select";
import { StatusBadge } from "../../components/settings/StatusBadge";
import { ApiError } from "../../services/api";
import { integrationsApi } from "../../services/integrations";
import { accountsApi } from "../../services/accounts";
import { notifyChannelsChanged } from "../../hooks/useChannels";
import type { Account } from "../../types/account";
import type { DiscoveredChannel, Integration } from "../../types/integration";

type Banner = { tone: "success" | "danger" | "info"; message: string } | null;

const PLATFORM_LABELS: Record<string, string> = {
  instagram: "Instagram",
  facebook: "Facebook",
  linkedin: "LinkedIn",
  twitter: "X (Twitter)",
  threads: "Threads",
  tiktok: "TikTok",
  mastodon: "Mastodon",
  youtube: "YouTube",
  bluesky: "Bluesky",
  pinterest: "Pinterest",
};

export function BufferIntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<Banner>(null);

  // add form
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // per-integration transient UI state
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rotation, setRotation] = useState<{ id: string; value: string } | null>(null);
  const [discovery, setDiscovery] = useState<Record<string, DiscoveredChannel[]>>({});
  const [selection, setSelection] = useState<Record<string, string[]>>({});
  const [targetAccount, setTargetAccount] = useState<Record<string, string>>({});
  const [quickBrand, setQuickBrand] = useState(false);
  const [quickBrandName, setQuickBrandName] = useState("");
  // The workspace the operator has chosen for each integration. Persisted only
  // on explicit save, never by testing the connection.
  const [pendingWorkspace, setPendingWorkspace] = useState<Record<string, string>>({});

  const reload = useCallback(async () => {
    try {
      // `manage` so a brand created moments ago is offered as an import target,
      // even before it has any channels.
      const [list, accs] = await Promise.all([integrationsApi.list(), accountsApi.manage()]);
      setIntegrations(list);
      setAccounts(accs);
    } catch {
      setBanner({ tone: "danger", message: "Could not load integrations from the backend." });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const defaultAccount = accounts[0]?.id ?? "";

  // ------------------------------------------------------------------ add
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setFormError(null);
    if (!token.trim()) {
      setFormError("Enter a Buffer access token.");
      return;
    }
    setSaving(true);
    try {
      const created = await integrationsApi.create({
        display_name: name.trim() || "Buffer",
        api_key: token.trim(),
      });
      // The token exists only in this request. Clear it immediately so it is
      // not left sitting in component state or React DevTools history.
      setToken("");
      setName("");
      setBanner({
        tone: created.status === "connected" ? "success" : "danger",
        message:
          created.status === "connected"
            ? `Connected to ${created.external_organization_name ?? "Buffer"}. Discover channels to finish setup.`
            : `Saved, but the connection failed: ${created.last_error_message ?? "unknown error"}`,
      });
      await reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Could not save the integration.");
    } finally {
      setSaving(false);
    }
  };

  // ------------------------------------------------------------------ actions
  const run = async (
    integrationId: string,
    action: () => Promise<unknown>,
    success: string,
  ) => {
    setBusyId(integrationId);
    setBanner(null);
    try {
      await action();
      setBanner({ tone: "success", message: success });
    } catch (err) {
      // The message is always surfaced. The previous version threw a plain Error
      // from inside `action`, which the `instanceof ApiError` check below then
      // discarded, so a failed connection test reported "The action failed."
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error && err.message
            ? err.message
            : "The action failed.";
      setBanner({ tone: "danger", message });
    } finally {
      setBusyId(null);
      await reload();
    }
  };

  /** Save the operator's chosen workspace. Never happens implicitly. */
  const saveWorkspace = async (integration: Integration, organizationId: string) => {
    if (!organizationId || organizationId === integration.external_organization_id) {
      setPendingWorkspace((prev) => ({ ...prev, [integration.id]: "" }));
      return;
    }
    await run(
      integration.id,
      () =>
        integrationsApi.update(integration.id, { organization_id: organizationId }),
      "Workspace selected. Channels will be discovered from this one.",
    );
    setPendingWorkspace((prev) => ({ ...prev, [integration.id]: "" }));
  };

  const discover = async (integration: Integration) => {
    setBusyId(integration.id);
    setBanner(null);
    try {
      const result = await integrationsApi.discover(integration.id);
      setDiscovery((prev) => ({ ...prev, [integration.id]: result.channels }));
      setSelection((prev) => ({
        ...prev,
        // Pre-select nothing: importing channels is a deliberate act.
        [integration.id]: (prev[integration.id] ?? []).filter((id) =>
          result.channels.some((c) => c.external_channel_id === id && !c.already_imported),
        ),
      }));
      setTargetAccount((prev) => ({ ...prev, [integration.id]: prev[integration.id] ?? defaultAccount }));
      setBanner({
        tone: "success",
        message: `Found ${result.total} channel(s) in ${result.organization_name ?? "the workspace"}.`,
      });
    } catch (err) {
      setBanner({
        tone: "danger",
        message: err instanceof ApiError ? err.message : "Channel discovery failed.",
      });
    } finally {
      setBusyId(null);
    }
  };

  const sync = async (integration: Integration) => {
    const ids = selection[integration.id] ?? [];
    const accountId = targetAccount[integration.id] ?? defaultAccount;
    if (!accountId) {
      setBanner({ tone: "danger", message: "Choose which brand these channels belong to." });
      return;
    }
    if (ids.length === 0) {
      setBanner({ tone: "danger", message: "Select at least one channel to import." });
      return;
    }
    await run(
      integration.id,
      () =>
        integrationsApi.sync(integration.id, {
          account_id: accountId,
          external_channel_ids: ids,
        }),
      `Imported ${ids.length} channel(s). They are now available in Quick Publisher and AI Automation.`,
    );
    setSelection((prev) => ({ ...prev, [integration.id]: [] }));
    // The import just changed the account's channel list, so every screen
    // holding a stale copy (Quick Publish, Dashboard, Accounts) has to refetch.
    notifyChannelsChanged();
  };

  /** Create a brand inline and immediately target it for import. */
  const createBrandAndSelect = async (integrationId: string): Promise<boolean> => {
    const brandName = quickBrandName.trim();
    if (!brandName) {
      setBanner({ tone: "danger", message: "Enter a name for the new brand." });
      return false;
    }
    try {
      const created = await accountsApi.create({ name: brandName, type: "brand" });
      setQuickBrandName("");
      await reload();
      setTargetAccount((prev) => ({ ...prev, [integrationId]: created.id }));
      return true;
    } catch (err) {
      setBanner({
        tone: "danger",
        message: err instanceof ApiError ? err.message : "Could not create the brand.",
      });
      return false;
    }
  };

  const remove = async (integration: Integration) => {
    if (
      !window.confirm(
        `Disconnect "${integration.display_name}"? Its ${integration.channel_count} channel(s) stay in your brands but stop being publishable until you reconnect.`,
      )
    ) {
      return;
    }
    await run(integration.id, () => integrationsApi.remove(integration.id), "Integration disconnected.");
    setDiscovery((prev) => {
      const next = { ...prev };
      delete next[integration.id];
      return next;
    });
  };

  const rotate = async (integration: Integration) => {
    const value = rotation?.value.trim() ?? "";
    if (!value) return;
    await run(
      integration.id,
      () => integrationsApi.rotate(integration.id, value),
      "Credential replaced and the connection was re-tested.",
    );
    setRotation(null);
  };

  const toggle = async (integration: Integration) => {
    await run(
      integration.id,
      () => integrationsApi.update(integration.id, { enabled: !integration.enabled }),
      integration.enabled ? "Integration disabled." : "Integration enabled.",
    );
  };

  const toggleSelected = (integrationId: string, channelId: string) => {
    setSelection((prev) => {
      const current = prev[integrationId] ?? [];
      return {
        ...prev,
        [integrationId]: current.includes(channelId)
          ? current.filter((id) => id !== channelId)
          : [...current, channelId],
      };
    });
  };

  const grouped = useMemo(
    () => ({ connected: integrations.filter((i) => i.status === "connected"), other: integrations.filter((i) => i.status !== "connected") }),
    [integrations],
  );

  if (loading) return <p className="muted">Loading integrations…</p>;

  return (
    <div className="stack">
      {banner && (
        <div className={`banner banner--${banner.tone}`} role="status">
          {banner.message}
        </div>
      )}

      <Card>
        <CardHeader
          title="Add a Buffer integration"
          description="Paste an access token from Buffer. It is encrypted in the backend, shown masked from then on, and never sent to the browser or to n8n."
        />
        <form className="settings__form" onSubmit={submit}>
          <Field label="Name" htmlFor="int-name" hint="For example: SortPi workspace. Helps when you run several.">
            <input
              id="int-name"
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="SortPi workspace"
              maxLength={160}
            />
          </Field>
          <Field
            label="Buffer access token"
            htmlFor="int-token"
            error={formError}
            hint="Create one in Buffer under Settings → Account → Developer settings. One token can reach several workspaces; all of them are listed after the connection test."
          >
            <input
              id="int-token"
              className="input"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste the access token"
            />
          </Field>
          <div>
            <Button type="submit" variant="primary" loading={saving}>
              Save and test connection
            </Button>
          </div>
        </form>
      </Card>

      {integrations.length === 0 && (
        <EmptyState
          title="No integrations yet"
          description="Add a Buffer access token above. Once it is connected you can discover its channels and attach them to any brand."
        />
      )}

      {[...grouped.connected, ...grouped.other].map((integration) => {
        const channels = discovery[integration.id] ?? [];
        const selected = selection[integration.id] ?? [];
        const isBusy = busyId === integration.id;
        const workspaces = integration.available_organizations ?? [];
        const pending = pendingWorkspace[integration.id] ?? "";
        const needsWorkspace = workspaces.length > 1 && !integration.external_organization_id;

        return (
          <Card key={integration.id}>
            <CardHeader
              title={integration.display_name}
              description={
                integration.external_organization_name
                  ? `Buffer workspace: ${integration.external_organization_name}`
                  : "Not bound to a workspace yet."
              }
              action={<StatusBadge status={integration.status} />}
            />

            {needsWorkspace && (
              <div className="banner banner--warning">
                {integration.last_error_message ??
                  "This token can reach several workspaces. Choose which one to publish through."}
              </div>
            )}

            {/* Workspace picker. Explicit by design: the token is validated
                read-only, then the operator picks, and only then is it saved.
                The previous version never offered this, so a multi-workspace
                token silently bound to whichever one Buffer listed first. */}
            {workspaces.length > 0 && (
              <div className="inline-form">
                <Select
                  aria-label={`Workspace for ${integration.display_name}`}
                  value={pending || integration.external_organization_id || ""}
                  options={workspaces.map((w) => ({
                    value: w.id,
                    label: w.name,
                    sublabel: w.id === integration.external_organization_id ? "current" : undefined,
                  }))}
                  onChange={(value) =>
                    setPendingWorkspace((prev) => ({ ...prev, [integration.id]: value }))
                  }
                />
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!pending || pending === integration.external_organization_id}
                  loading={isBusy}
                  onClick={() => saveWorkspace(integration, pending)}
                >
                  Use this workspace
                </Button>
              </div>
            )}

            <dl className="settings__facts">
              <div>
                <dt>Credential</dt>
                <dd>
                  {integration.credential_stored ? (
                    <>
                      Stored {integration.credential_hint ? `(${integration.credential_hint})` : ""}
                    </>
                  ) : (
                    "Not stored"
                  )}
                </dd>
              </div>
              <div>
                <dt>Channels</dt>
                <dd>
                  {integration.channel_count} linked · {integration.active_channel_count} active
                </dd>
              </div>
              <div>
                <dt>Last tested</dt>
                <dd>{formatWhen(integration.last_tested_at)}</dd>
              </div>
              <div>
                <dt>Last synced</dt>
                <dd>{formatWhen(integration.last_sync_at)}</dd>
              </div>
            </dl>

            {integration.last_error_message && (
              <div className="banner banner--danger">
                {integration.last_error_message}
              </div>
            )}

            <div className="button-row">
              <Button
                size="sm"
                onClick={() => discover(integration)}
                loading={isBusy && channels.length === 0}
              >
                {channels.length > 0 ? "Refresh channels" : "Discover channels"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  run(
                    integration.id,
                    () => integrationsApi.test(integration.id).then((r) => {
                      if (!r.ok) throw new Error(r.message);
                      return r;
                    }),
                    "Connection test succeeded.",
                  )
                }
                loading={isBusy}
              >
                Test connection
              </Button>
              <Button size="sm" variant="ghost" onClick={() => toggle(integration)}>
                {integration.enabled ? "Disable" : "Enable"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setRotation({ id: integration.id, value: "" })}
              >
                Rotate token
              </Button>
              <Button size="sm" variant="danger" onClick={() => remove(integration)}>
                Disconnect
              </Button>
            </div>

            {rotation?.id === integration.id && (
              <div className="inline-form">
                <input
                  className="input"
                  type="password"
                  autoComplete="off"
                  placeholder="New Buffer access token"
                  value={rotation.value}
                  onChange={(e) => setRotation({ id: integration.id, value: e.target.value })}
                />
                <Button size="sm" variant="primary" onClick={() => rotate(integration)}>
                  Replace and re-test
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setRotation(null)}>
                  Cancel
                </Button>
              </div>
            )}

            {channels.length > 0 && (
              <div className="discovery">
                <div className="discovery__head">
                  <div>
                    <h4 className="card__title">Available channels</h4>
                    <p className="card__description">
                      Listed live from Buffer. Select the ones to import and choose the brand they
                      belong to.
                    </p>
                  </div>
                  <div className="discovery__actions">
                    <Select
                      aria-label="Target brand"
                      value={targetAccount[integration.id] ?? defaultAccount}
                      options={accounts.map((a) => ({ value: a.id, label: a.name }))}
                      onChange={(value) =>
                        setTargetAccount((prev) => ({ ...prev, [integration.id]: value }))
                      }
                    />
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setQuickBrand((prev) => !prev)}
                    >
                      New brand
                    </Button>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={selected.length === 0}
                      onClick={() => sync(integration)}
                    >
                      {selected.length
                        ? `Import ${selected.length} selected`
                        : "Import selected"}
                    </Button>
                  </div>
                </div>

                {quickBrand && (
                  <div className="inline-form">
                    <input
                      className="input"
                      placeholder="New brand name"
                      value={quickBrandName}
                      onChange={(e) => setQuickBrandName(e.target.value)}
                    />
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={async () => {
                        const created = await createBrandAndSelect(integration.id);
                        if (created) setQuickBrand(false);
                      }}
                    >
                      Create and use
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setQuickBrand(false)}>
                      Cancel
                    </Button>
                  </div>
                )}

                <ul className="channel-picker">
                  {channels.map((channel) => {
                    // Importing is blocked for anything that would come in
                    // unusable. Previously only `locked` and `disconnected` were
                    // considered, so an unsupported platform or a queue-paused
                    // profile could be imported and then behaved oddly forever.
                    const reasons: string[] = [];
                    if (channel.locked) reasons.push("locked in Buffer");
                    if (channel.disconnected) reasons.push("disconnected");
                    if (!channel.supported) reasons.push("not supported by this engine");
                    if (channel.queue_paused) reasons.push("queue is paused");
                    const unusable = reasons.length > 0;
                    const imported = channel.already_imported;
                    return (
                      <li key={channel.external_channel_id} className="channel-option">
                        <label className="channel-option__label">
                          <input
                            type="checkbox"
                            checked={selected.includes(channel.external_channel_id)}
                            // Imported channels are skipped rather than blocked,
                            // so a full re-sync stays possible.
                            disabled={unusable}
                            onChange={() => toggleSelected(integration.id, channel.external_channel_id)}
                          />
                          <span className="channel-option__name">
                            {channel.display_name || channel.name}
                            <small>{channel.name}</small>
                          </span>
                        </label>
                        <span className="channel-option__meta">
                          <Badge tone="neutral">
                            {PLATFORM_LABELS[channel.service] ?? channel.service}
                          </Badge>
                          {imported && <Badge tone="success">Already imported</Badge>}
                          {channel.locked && <Badge tone="danger">Locked</Badge>}
                          {channel.disconnected && <Badge tone="warning">Disconnected</Badge>}
                          {channel.queue_paused && <Badge tone="info">Queue paused</Badge>}
                          {!channel.supported && <Badge tone="warning">Unsupported here</Badge>}
                          {unusable && (
                            <span className="muted small">Cannot be imported: {reasons.join(", ")}.</span>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
