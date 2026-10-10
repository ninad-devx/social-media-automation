import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, CardHeader, EmptyState, Field } from "../components/ui";
import { Select } from "../components/ui/Select";
import { accountsApi } from "../services/accounts";
import { instructionsApi, providersApi } from "../services/ai";
import { useAccounts } from "../hooks/useAccounts";
import type {
  AccountInstructions,
  AiProvider,
  ChannelInstructions,
  ProviderCatalogueItem,
} from "../types/ai";
import type { Channel } from "../types/channel";

type Tab = "providers" | "global" | "brand" | "channel";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "providers", label: "AI providers" },
  { id: "global", label: "Global instructions" },
  { id: "brand", label: "Brand profiles" },
  { id: "channel", label: "Channel instructions" },
];

export function AiSettingsPage() {
  const [tab, setTab] = useState<Tab>("providers");

  return (
    <div className="page page--narrow">
      <header className="page__header">
        <div>
          <h1>AI settings</h1>
          <p className="page__subtitle">
            Configure the model provider and the instructions that shape every draft.
          </p>
        </div>
      </header>

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`tabs__tab ${tab === t.id ? "is-active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "providers" && <ProvidersTab />}
      {tab === "global" && <GlobalTab />}
      {tab === "brand" && <BrandTab />}
      {tab === "channel" && <ChannelTab />}
    </div>
  );
}

/* ==========================================================================
   Providers
   ========================================================================== */
function ProvidersTab() {
  const [catalogue, setCatalogue] = useState<ProviderCatalogueItem[] | null>(null);
  const [providers, setProviders] = useState<AiProvider[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<AiProvider | "new" | null>(null);

  const load = useCallback(async () => {
    try {
      const [cat, list] = await Promise.all([providersApi.catalogue(), providersApi.list()]);
      setCatalogue(cat);
      setProviders(list);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const locked = providers?.some((p) => !p.encryption_available) ?? false;

  return (
    <>
      {locked && (
        <Card className="notice notice--warn">
          <strong>Provider storage is not configured.</strong>
          <p>
            The backend is missing <code>AI_ENCRYPTION_KEY</code>. Add one to{" "}
            <code>backend/.env</code> and restart before saving any key.
          </p>
        </Card>
      )}

      <Card>
        <CardHeader
          title="Connected providers"
          description="Keys are encrypted in the backend and never sent to the browser again."
          action={
            <Button variant="primary" onClick={() => setEditing("new")}>
              Add provider
            </Button>
          }
        />

        {error && <p className="notice notice--error">{error}</p>}
        {notice && <p className="notice notice--ok">{notice}</p>}

        {!providers && !error && <p className="muted">Loading providersâ€¦</p>}

        {providers && providers.length === 0 && (
          <EmptyState
            title="No provider connected"
            description="Add an AI provider to start generating drafts."
          />
        )}

        <ul className="stack">
          {providers?.map((p) => (
            <ProviderRow
              key={p.id}
              provider={p}
              onEdit={() => setEditing(p)}
              onChanged={async (msg) => {
                setNotice(msg);
                await load();
              }}
            />
          ))}
        </ul>
      </Card>

      {editing && (
        <ProviderDialog
          catalogue={catalogue ?? []}
          provider={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            setNotice("Provider saved.");
            await load();
          }}
          busy={busy}
          setBusy={setBusy}
        />
      )}
    </>
  );
}

function ProviderRow({
  provider,
  onEdit,
  onChanged,
}: {
  provider: AiProvider;
  onEdit: () => void;
  onChanged: (message: string) => void | Promise<void>;
}) {
  const [testing, setTesting] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [models, setModels] = useState<string[] | null>(null);

  const test = async () => {
    setTesting(true);
    try {
      const result = await providersApi.test(provider.id);
      await onChanged(
        result.ok
          ? `Connected to ${provider.display_name}.`
          : (result.detail ?? "The provider rejected this key.")
      );
    } catch (err) {
      await onChanged((err as Error).message);
    } finally {
      setTesting(false);
    }
  };

  const loadModels = async () => {
    setLoadingModels(true);
    try {
      const list = await providersApi.models(provider.id);
      setModels(list.models);
      if (!list.ok) await onChanged(list.detail ?? "Could not load models.");
    } catch (err) {
      await onChanged((err as Error).message);
    } finally {
      setLoadingModels(false);
    }
  };

  return (
    <li className="row-card">
      <div className="row-card__main">
        <div className="row-card__title">
          <strong>{provider.display_name}</strong>
          <Badge tone={provider.enabled ? "success" : "neutral"}>
            {provider.enabled ? "Enabled" : "Disabled"}
          </Badge>
          {provider.is_default && <Badge tone="info">Default</Badge>}
          {provider.last_status === "ok" && <Badge tone="success">Connected</Badge>}
          {provider.last_status === "error" && <Badge tone="danger">Error</Badge>}
        </div>
        <p className="row-card__meta">
          {provider.provider_name}
          {provider.model ? ` Â· ${provider.model}` : ""}
          {provider.api_key_masked ? ` Â· ${provider.api_key_masked}` : " Â· no key saved"}
        </p>
        {provider.last_error_code && (
          <p className="row-card__meta row-card__meta--warn">{provider.last_error_code}</p>
        )}
        {models && models.length > 0 && (
          <p className="row-card__meta">
            Available models: {models.slice(0, 6).join(", ")}
            {models.length > 6 ? "â€¦" : ""}
          </p>
        )}
      </div>
      <div className="row-card__actions">
        {provider.supports_model_discovery && (
          <Button size="sm" loading={loadingModels} onClick={loadModels}>
            Models
          </Button>
        )}
        <Button size="sm" loading={testing} onClick={test}>
          Test
        </Button>
        <Button size="sm" onClick={onEdit}>
          Edit
        </Button>
      </div>
    </li>
  );
}

function ProviderDialog({
  catalogue,
  provider,
  onClose,
  onSaved,
  busy,
  setBusy,
}: {
  catalogue: ProviderCatalogueItem[];
  provider: AiProvider | null;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
  busy: boolean;
  setBusy: (v: boolean) => void;
}) {
  const [slug, setSlug] = useState(provider?.provider_slug ?? "openai");
  const [displayName, setDisplayName] = useState(provider?.display_name ?? "");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [model, setModel] = useState(provider?.model ?? "");
  const [error, setError] = useState<string | null>(null);

  // Live probe state, so the user can see the key work before saving.
  const [models, setModels] = useState<string[]>([]);
  const [probing, setProbing] = useState(false);
  const [probeNote, setProbeNote] = useState<string | null>(null);
  const [probeOk, setProbeOk] = useState<boolean | null>(null);

  const spec = useMemo(() => catalogue.find((c) => c.slug === slug), [catalogue, slug]);

  // Fill sensible defaults whenever the provider changes on a new provider.
  useEffect(() => {
    if (provider || !spec) return;
    setDisplayName(spec.display_name);
    setBaseUrl(spec.default_base_url);
    setModel(spec.default_model);
  }, [spec, provider]);

  /**
   * Paste a key -> identify the provider and load its real model list.
   * This is what stops the model list being empty: the models come from the
   * key itself, so they are models that key can actually use.
   */
  const detect = useCallback(
    async (key: string, overrideSlug?: string) => {
      const probeSlug = overrideSlug ?? (overrideSlug === "" ? undefined : slug);
      setProbing(true);
      setProbeNote(null);
      setProbeOk(null);
      try {
        const result = await providersApi.detect(key, probeSlug, baseUrl || undefined);
        if (result.detected_slug !== slug) {
          setSlug(result.detected_slug);
          setBaseUrl(result.default_base_url || baseUrl);
        }
        setModels(result.models);
        setProbeOk(result.credentials_ok);
        setProbeNote(result.detail || result.reason);
        if (!provider) {
          setDisplayName((current) => current || result.display_name);
          // Prefer a model this key proved it can use.
          if (result.models.length) {
            setModel(
              result.default_model && result.models.includes(result.default_model)
                ? result.default_model
                : result.models[0]
            );
          }
        }
      } catch (err) {
        setProbeNote((err as Error).message);
        setProbeOk(false);
      } finally {
        setProbing(false);
      }
    },
    [slug, baseUrl, provider]
  );

  // Debounced auto-detect so typing a key feels instant without hammering
  // the provider on every keystroke.
  useEffect(() => {
    const key = apiKey.trim();
    if (provider || key.length < 12) {
      setModels([]);
      setProbeNote(null);
      setProbeOk(null);
      return;
    }
    const timer = window.setTimeout(() => void detect(key), 600);
    return () => window.clearTimeout(timer);
  }, [apiKey, detect, provider]);

  const submit = async () => {
    setError(null);
    if (!provider && !apiKey.trim()) {
      setError("Enter an API key.");
      return;
    }
    if (spec?.requires_base_url && !baseUrl.trim()) {
      setError("This provider needs a base URL.");
      return;
    }
    if (!model.trim()) {
      setError("Choose a model. Pick one from the list or type it in.");
      return;
    }
    setBusy(true);
    try {
      if (provider) {
        // The key is omitted when left blank so an edit never clears it.
        const payload: Record<string, unknown> = {
          display_name: displayName,
          base_url: baseUrl,
          model,
        };
        if (apiKey.trim()) payload.api_key = apiKey.trim();
        await providersApi.update(provider.id, payload);
      } else {
        await providersApi.create({
          provider_slug: slug,
          display_name: displayName || spec?.display_name || slug,
          api_key: apiKey.trim(),
          base_url: baseUrl || null,
          model: model || null,
          make_default: true,
        });
      }
      // Never keep the key in component state after a successful save.
      setApiKey("");
      await onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!provider) return;
    setBusy(true);
    try {
      await providersApi.remove(provider.id);
      await onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const modelOptions = useMemo(
    () =>
      (models.includes(model) ? models : [model, ...models])
        .filter(Boolean)
        .map((m) => ({ value: m, label: m })),
    [models, model]
  );

  return (
    <Modal title={provider ? `Edit ${provider.display_name}` : "Add a provider"} onClose={onClose}>
      {error && <p className="notice notice--error">{error}</p>}

      <Field
        label={provider?.key_label ?? spec?.key_label ?? "API key"}
        htmlFor="provider-key"
        hint={
          provider
            ? "Leave blank to keep the stored key."
            : "Paste your key. The provider and its models are detected automatically. It is stored encrypted and never shown again."
        }
      >
        <input className="input"
          id="provider-key"
          type="password"
          autoComplete="off"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={provider ? "••••••••" : "Paste your API key"}
        />
      </Field>

      {probing && <p className="muted">Checking the key and loading models…</p>}

      {!probing && probeNote && (
        <p className={`notice notice--${probeOk === false ? "error" : probeOk ? "ok" : "info"}`}>
          {probeNote}
        </p>
      )}

      {!probing && probeOk === false && spec?.requires_base_url && (
        <p className="notice notice--warn">
          This key has no recognisable prefix, so it may need a base URL for a custom or
          OpenAI-compatible endpoint.
        </p>
      )}

      <Field label="Provider" htmlFor="provider-slug" hint="Detected from the key, but you can change it.">
        <Select
          id="provider-slug"
          value={slug}
          onChange={(next) => {
            setSlug(next);
            const nextSpec = catalogue.find((c) => c.slug === next);
            setBaseUrl(nextSpec?.default_base_url ?? "");
            setModel(nextSpec?.default_model ?? "");
            setModels([]);
            setProbeNote(null);
            setProbeOk(null);
            // Re-probe with the same key against the newly chosen provider.
            if (apiKey.trim()) void detect(apiKey.trim(), next);
          }}
          options={catalogue.map((c) => ({ value: c.slug, label: c.display_name }))}
        />
      </Field>

      <Field label="Display name" htmlFor="provider-name">
        <input className="input"
          id="provider-name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="My OpenAI"
        />
      </Field>

      <Field
        label="Base URL"
        htmlFor="provider-base"
        hint={spec?.default_base_url || "Required for custom or OpenAI-compatible endpoints."}
      >
        <input className="input"
          id="provider-base"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={spec?.default_base_url || "https://…"}
        />
      </Field>

      <Field
        label="Model"
        htmlFor="provider-model"
        hint={
          models.length
            ? `${models.length} model(s) available with this key.`
            : "Type a model name, or paste a key to load the list."
        }
      >
        <input className="input"
          id="provider-model"
          list="provider-model-options"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={spec?.default_model || "model-name"}
        />
        <datalist id="provider-model-options">
          {modelOptions.map((o) => (
            <option key={o.value} value={o.value} />
          ))}
        </datalist>
      </Field>

      {models.length > 0 && (
        <div className="model-chips">
          {models.slice(0, 12).map((m) => (
            <button
              key={m}
              type="button"
              className={`chip ${model === m ? "is-active" : ""}`}
              onClick={() => setModel(m)}
            >
              {m}
            </button>
          ))}
        </div>
      )}

      <div className="modal__actions">
        {provider && (
          <Button variant="danger" onClick={remove} loading={busy}>
            Remove
          </Button>
        )}
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={submit} loading={busy} disabled={probing}>
          Save
        </Button>
      </div>
    </Modal>
  );
}
/* ==========================================================================
   Global instructions
   ========================================================================== */
function GlobalTab() {
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    instructionsApi
      .getGlobal(controller.signal)
      .then((r) => setContent(r.content))
      .catch((err) => setError((err as Error).message));
    return () => controller.abort();
  }, []);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await instructionsApi.putGlobal(content);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader
        title="Global instructions"
        description="Defaults that apply to every brand. Channel rules override these."
      />
      {error && <p className="notice notice--error">{error}</p>}
      {saved && <p className="notice notice--ok">Saved.</p>}
      <Field label="Instructions" htmlFor="global-instructions" hint="Plain language. For example: always use British English; never use exclamation marks.">
        <textarea className="textarea"
          id="global-instructions"
          rows={10}
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
      </Field>
      <Button variant="primary" onClick={save} loading={saving}>
        Save instructions
      </Button>
    </Card>
  );
}

/* ==========================================================================
   Brand profiles
   ========================================================================== */
const BRAND_FIELDS: Array<{ key: keyof AccountInstructions; label: string; hint?: string }> = [
  { key: "description", label: "What this brand does", hint: "One or two plain sentences." },
  { key: "industry", label: "Industry / niche" },
  { key: "target_audience", label: "Target audience" },
  { key: "products", label: "Products or services" },
  { key: "voice", label: "Brand voice", hint: "e.g. calm, direct, technical, warm" },
  { key: "goals", label: "Marketing goals" },
  { key: "preferred_cta", label: "Preferred calls to action" },
  { key: "terminology", label: "Preferred terminology", hint: "Words to always use." },
  { key: "avoid_words", label: "Words to avoid", hint: "Comma separated." },
  { key: "hashtag_preferences", label: "Hashtag preferences" },
  { key: "language", label: "Language" },
  { key: "extra_context", label: "Additional context" },
  { key: "instructions", label: "Free-text brand instructions" },
];

function BrandTab() {
  const { data: accounts } = useAccounts();
  const [accountId, setAccountId] = useState<string>("");
  const [draft, setDraft] = useState<AccountInstructions | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accountId && accounts?.length) setAccountId(accounts[0].id);
  }, [accounts, accountId]);

  useEffect(() => {
    if (!accountId) return;
    const controller = new AbortController();
    setDraft(null);
    instructionsApi
      .getAccount(accountId, controller.signal)
      .then(setDraft)
      .catch((err) => setError((err as Error).message));
    return () => controller.abort();
  }, [accountId]);

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const payload = Object.fromEntries(
        BRAND_FIELDS.map(({ key }) => [key, draft[key] ?? ""])
      );
      await instructionsApi.putAccount(draft.account_id, payload);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (!accounts?.length) {
    return <EmptyState title="No accounts yet" description="Add an account before setting brand instructions." />;
  }

  return (
    <Card>
      <CardHeader
        title="Brand profiles"
        description="Each brand is isolated. One brand's context is never mixed into another's posts."
      />

      <Field label="Brand" htmlFor="brand-select">
        <Select
          id="brand-select"
          value={accountId}
          onChange={setAccountId}
          options={accounts.map((a) => ({ value: a.id, label: a.name }))}
        />
      </Field>

      {error && <p className="notice notice--error">{error}</p>}
      {saved && <p className="notice notice--ok">Saved.</p>}

      {!draft && <p className="muted">Loadingâ€¦</p>}

      {draft && (
        <>
          {BRAND_FIELDS.map(({ key, label, hint }) => (
            <Field key={String(key)} label={label} hint={hint} htmlFor={`brand-${String(key)}`}>
              <textarea className="textarea"
                id={`brand-${String(key)}`}
                rows={key === "description" || key === "instructions" || key === "extra_context" ? 3 : 2}
                value={(draft[key] as string | null) ?? ""}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            </Field>
          ))}
          <Button variant="primary" onClick={save} loading={saving}>
            Save brand profile
          </Button>
        </>
      )}
    </Card>
  );
}

/* ==========================================================================
   Channel instructions
   ========================================================================== */
function ChannelTab() {
  const { data: accounts } = useAccounts();
  const [accountId, setAccountId] = useState("");
  const [channels, setChannels] = useState<Channel[]>([]);
  const [channelId, setChannelId] = useState("");
  const [draft, setDraft] = useState<ChannelInstructions | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accountId && accounts?.length) setAccountId(accounts[0].id);
  }, [accounts, accountId]);

  useEffect(() => {
    if (!accountId) return;
    const controller = new AbortController();
    accountsApi
      .channels(accountId, controller.signal)
      .then(setChannels)
      .catch((err) => setError((err as Error).message));
    return () => controller.abort();
  }, [accountId]);

  useEffect(() => {
    if (!channels.length) {
      setChannelId("");
      return;
    }
    if (!channels.some((c) => c.id === channelId)) setChannelId(channels[0].id);
  }, [channels, channelId]);

  useEffect(() => {
    if (!channelId) return;
    const controller = new AbortController();
    setDraft(null);
    instructionsApi
      .getChannel(channelId, controller.signal)
      .then(setDraft)
      .catch((err) => setError((err as Error).message));
    return () => controller.abort();
  }, [channelId]);

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      await instructionsApi.putChannel(draft.channel_id, draft.instructions ?? "");
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await instructionsApi.resetChannel(draft.channel_id);
      setDraft({ ...draft, instructions: null, configured: false });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (!accounts?.length) {
    return <EmptyState title="No accounts yet" description="Add an account before setting channel instructions." />;
  }

  return (
    <Card>
      <CardHeader
        title="Channel instructions"
        description="The most specific layer. A rule here overrides the brand and global rules."
      />

      <Field label="Brand" htmlFor="channel-brand">
        <Select
          id="channel-brand"
          value={accountId}
          onChange={setAccountId}
          options={accounts.map((a) => ({ value: a.id, label: a.name }))}
        />
      </Field>

      {channels.length > 1 && (
        <Field label="Channel" htmlFor="channel-select">
          <Select
            id="channel-select"
            value={channelId}
            onChange={setChannelId}
            options={channels.map((c) => ({ value: c.id, label: c.display_name ?? c.id }))}
          />
        </Field>
      )}

      {error && <p className="notice notice--error">{error}</p>}
      {saved && <p className="notice notice--ok">Saved.</p>}

      {!draft && <p className="muted">Loadingâ€¦</p>}

      {draft && (
        <>
          <p className="muted">
            {draft.platform_label} Â· {draft.display_name}
          </p>
          <Field
            label="Channel instructions"
            htmlFor="channel-instructions"
            hint="Leave empty to inherit the brand and global rules."
          >
            <textarea className="textarea"
              id="channel-instructions"
              rows={6}
              value={draft.instructions ?? ""}
              onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
            />
          </Field>
          <div className="row-actions">
            <Button variant="primary" onClick={save} loading={saving}>
              Save
            </Button>
            {draft.configured && <Button onClick={reset}>Reset to inherited</Button>}
          </div>
        </>
      )}
    </Card>
  );
}

/* ---- shared bits ---- */
function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal__title">{title}</h2>
        {children}
      </div>
    </div>
  );
}
