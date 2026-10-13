import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DateTime } from "luxon";
import { Card, Button, Field, Badge } from "../components/ui";
import { AccountChannelSelectors } from "../components/quick-publish/AccountChannelSelectors";
import { MediaPicker } from "../components/quick-publish/MediaPicker";
import {
  SchedulePicker,
  buildScheduledAt,
  isFuture,
  nextSensibleSlot,
} from "../components/quick-publish/SchedulePicker";
import { PostTypePicker } from "../components/quick-publish/PostTypePicker";
import { PostPreview } from "../components/quick-publish/PostPreview";
import { PublishError, PublishSuccess } from "../components/quick-publish/PublishFeedback";
import { useAccounts } from "../hooks/useAccounts";
import { useChannels } from "../hooks/useChannels";
import { usePublish } from "../hooks/usePublish";
import { usePlatformCapability } from "../hooks/usePlatformCapability";
import { campaignsApi } from "../services/ai";
import { platformMeta } from "../lib/platforms";
import { channelStatus } from "../types/channel";
import type { MediaItem, PublishMode } from "../types/publishing";

const DEFAULT_TIMEZONE = import.meta.env.VITE_DEFAULT_TIMEZONE ?? "Asia/Dhaka";

/** One composed post waiting to be saved into a named campaign. */
interface DraftRow {
  key: string;
  account_id: string;
  channel_id: string;
  content: string;
  media: Array<{ type?: string; url?: string; alt_text?: string | null }>;
  post_type: string | null;
}

export function QuickPublishPage() {
  const { data: accounts, loading: accountsLoading } = useAccounts();

  const [accountId, setAccountId] = useState<string | null>(null);
  const [channelId, setChannelId] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [publishMode, setPublishMode] = useState<PublishMode>("NOW");
  const [postType, setPostType] = useState<string | null>(null);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [timezone, setTimezone] = useState(DEFAULT_TIMEZONE);

  const { channels } = useChannels(accountId);
  const { publishing, result, error, publish, reset } = usePublish();

  const channel = useMemo(
    () => channels.find((c) => c.id === channelId) ?? null,
    [channels, channelId],
  );
  const account = useMemo(
    () => accounts?.find((a) => a.id === accountId) ?? null,
    [accounts, accountId],
  );

  const meta = platformMeta(channel?.platform);
  const { capability } = usePlatformCapability(channel?.platform);
  const channelHealth = channel ? channelStatus(channel) : null;
  const trimmedLength = content.trim().length;

  // Preselect the first account once, so the page is usable immediately.
  useEffect(() => {
    if (!accountId && accounts?.length) setAccountId(accounts[0].id);
  }, [accounts, accountId]);

  // Default a future date/time the moment scheduling is chosen.
  useEffect(() => {
    if (publishMode !== "SCHEDULE") return;
    if (date && time) return;
    const slot = nextSensibleSlot(timezone);
    setDate(slot.date);
    setTime(slot.time);
  }, [publishMode, date, time, timezone]);

  const minDate = DateTime.now().toFormat("yyyy-MM-dd");

  // ---- gating, all derived from the backend's rules ---------------------
  const scheduleValid = publishMode === "NOW" || isFuture(date, time, timezone);
  const contentValid = trimmedLength > 0;

  // Eligibility is the backend's verdict, not a re-derivation. The backend
  // refuses the publish anyway; this only avoids letting the user get that far.
  const channelUsable = Boolean(channel?.publishable);
  const channelBlockReason = channelHealth?.detail ?? null;

  const postTypeSupported =
    postType === null ||
    capability === null ||
    capability.post_types.some((t) => t.key === postType);

  const postTypeSpec = postType
    ? (capability?.post_types.find((t) => t.key === postType) ?? null)
    : null;
  const scheduleOnly = Boolean(postTypeSpec?.requires_schedule);

  // Media rules come from the capability endpoint, so they cannot drift from
  // what the API will accept.
  //
  // "Required" is the greater of what the platform demands and what the chosen
  // format demands. Reading only `capability.requires_media` let Facebook Reel
  // and Story, and Twitter Video, be submitted with no media at all: the
  // platform flag is false because a plain feed post can be text-only, so the
  // per-format `min_assets` was invisible here and the backend rejected the
  // attempt only after the whole form had been filled in.
  const requiredMedia =
    Boolean(capability?.requires_media) || Boolean(postTypeSpec?.min_assets);
  const minAssets = Math.max(
    capability?.requires_media ? 1 : 0,
    postTypeSpec?.min_assets ?? 0,
  );
  const maxAssets = resolveMaxAssets(capability, postType);
  const mediaValid = media.length >= minAssets;
  const mediaCountValid = maxAssets === null || media.length <= maxAssets;
  const textValid = capability?.text_limit == null || trimmedLength <= capability.text_limit;

  // A type that requires scheduling cannot be shared immediately.
  useEffect(() => {
    if (scheduleOnly && publishMode === "NOW") setPublishMode("SCHEDULE");
  }, [scheduleOnly, publishMode]);

  // Reset the post type when the channel changes: a reel on one platform is
  // meaningless on the next, and the capability list differs.
  useEffect(() => {
    setPostType(null);
  }, [channelId]);

  const canSubmit =
    Boolean(accountId) &&
    Boolean(channelId) &&
    contentValid &&
    mediaValid &&
    mediaCountValid &&
    textValid &&
    postTypeSupported &&
    scheduleValid &&
    channelUsable &&
    !publishing;

  const fieldError = (field: string) => (error?.field === field ? error.message : null);

  const submit = () => {
    if (!canSubmit || !accountId || !channelId) return;
    void publish({
      account_id: accountId,
      channel_id: channelId,
      content: content.trim(),
      media,
      publish_mode: publishMode,
      scheduled_at: publishMode === "SCHEDULE" ? buildScheduledAt(date, time, timezone) : null,
      timezone,
      post_type: postType,
    });
  };

  const onResultDismiss = () => {
    reset();
    setContent("");
    setMedia([]);
  };

  /* ---- draft campaign ------------------------------------------------- */
  const [drafts, setDrafts] = useState<DraftRow[]>([]);
  const [draftName, setDraftName] = useState("");
  const [savingDraft, setSavingDraft] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [draftSaved, setDraftSaved] = useState<string | null>(null);

  const addToDraft = () => {
    if (!accountId || !channelId || !contentValid) return;
    const key = `${accountId}::${channelId}`;
    setDrafts((rows) => {
      const rest = rows.filter((r) => r.key !== key);
      return [
        ...rest,
        {
          key,
          account_id: accountId,
          channel_id: channelId,
          content: content.trim(),
          media: media.map((m) => ({ type: m.type, url: m.url, alt_text: m.alt_text })),
          post_type: postType,
        },
      ];
    });
    setContent("");
    setMedia([]);
    setDraftError(null);
    setDraftSaved(null);
  };

  const loadDraftIntoComposer = (row: DraftRow) => {
    setAccountId(row.account_id);
    setChannelId(row.channel_id);
    setContent(row.content);
    setMedia(row.media as MediaItem[]);
    setPostType(row.post_type);
  };

  const saveDraftCampaign = async () => {
    if (!drafts.length) return;
    setSavingDraft(true);
    setDraftError(null);
    try {
      const campaign = await campaignsApi.create({
        name: draftName.trim() || undefined,
        topic: draftName.trim() || "Campaign draft",
        drafts: drafts.map((d) => ({
          account_id: d.account_id,
          channel_id: d.channel_id,
          content: d.content,
          media: d.media,
          post_type: d.post_type,
        })),
        targets: [],
      });
      setDraftSaved(campaign.id);
      setDrafts([]);
      setDraftName("");
    } catch (err) {
      setDraftError((err as Error).message);
    } finally {
      setSavingDraft(false);
    }
  };

  const overLimit = capability?.text_limit != null && trimmedLength > capability.text_limit;

  return (
    <div className="page page--publish">
      <header className="page__header">
        <div>
          <h1>Quick Publish</h1>
          <p className="page__subtitle">
            Write once, choose where it goes, and publish or schedule it.
          </p>
        </div>
      </header>

      {result && (
        <PublishSuccess result={result} onDismiss={onResultDismiss} />
      )}

      {error && !result && (
        <PublishError
          message={error.message}
          code={error.code}
          requestId={error.requestId}
          onRetry={submit}
          onDismiss={reset}
        />
      )}

      <div className="publish-grid">
        <div className="publish-col publish-col--main">
          <Card>
            <AccountChannelSelectors
              accountId={accountId}
              channelId={channelId}
              onAccountChange={(id) => {
                setAccountId(id);
                setChannelId(null);
                reset();
              }}
              onChannelChange={(id) => {
                setChannelId(id);
                reset();
              }}
            />
            {accountsLoading && !accounts && <p className="publish__note">Loading accounts…</p>}
            {accounts && accounts.length === 0 && accountId && (
              <p className="publish__note">
                No brands are available yet. Create one in Settings, then import its channels.
              </p>
            )}
            {channels.length === 0 && accountId && !accountsLoading && (
              <p className="publish__note">
                This brand has no active channels yet. Import one from Settings → Buffer
                Integrations.
              </p>
            )}
            {channel && !channelUsable && channelBlockReason && (
              <p className="notice notice--error" role="status">
                {channelBlockReason}
              </p>
            )}
          </Card>

          <Card>
            <div className="publish__section-head">
              <h2 className="publish__section-title">Your post</h2>
              {channel && (
                <Badge tone={channelUsable ? "neutral" : "warning"}>
                  {meta.label}
                  {requiredMedia && " · media required"}
                </Badge>
              )}
            </div>

            {channel && (
              <PostTypePicker
                capability={capability}
                value={postType}
                onChange={(next) => {
                  setPostType(next);
                  reset();
                }}
                disabled={publishing}
                error={
                  error?.code === "POST_TYPE_UNSUPPORTED" ? error.message : null
                }
              />
            )}

            <Field
              label="Content"
              htmlFor="post-content"
              error={fieldError("content") ?? (error && !error.field ? error.message : null)}
            >
              <div className="editor">
                <textarea
                  id="post-content"
                  className="editor__area"
                  value={content}
                  onChange={(e) => {
                    setContent(e.target.value);
                    if (error) reset();
                  }}
                  placeholder="Write your content…"
                  rows={7}
                  maxLength={65000}
                  disabled={publishing}
                  autoFocus
                />
                <div className="editor__footer">
                  <span
                    className={`editor__count ${overLimit ? "is-over" : ""}`}
                    aria-live="polite"
                  >
                    {trimmedLength.toLocaleString()}
                    {capability?.text_limit != null &&
                      ` / ${capability.text_limit.toLocaleString()} characters`}
                  </span>
                </div>
              </div>
            </Field>

            <div className="publish__media">
              <span className="selector__label" id="media-label">
                Media
                {requiredMedia && <span className="field__optional">Required</span>}
              </span>
              <MediaPicker
                media={media}
                onChange={setMedia}
                disabled={publishing}
                requiresMedia={requiredMedia}
                maxItems={maxAssets}
                acceptsVideo={
                  postTypeSpec ? postTypeSpec.allows_video : (capability?.supports_video ?? true)
                }
                acceptsImage={
                  postTypeSpec ? postTypeSpec.allows_image : (capability?.supports_image ?? true)
                }
                error={fieldError("media") ?? (error?.code === "MEDIA_INVALID" ? error.message : null)}
              />
              {!mediaCountValid && maxAssets !== null && (
                <p className="field__error" role="alert">
                  {meta.label} accepts at most {maxAssets} attachment
                  {maxAssets === 1 ? "" : "s"} for this format. You have {media.length}.
                </p>
              )}
            </div>
          </Card>

          <Card>
            <div className="publish__section-head">
              <h2 className="publish__section-title">Publishing</h2>
            </div>

            <div className="segmented segmented--block" role="group" aria-label="Publish mode">
              <button
                type="button"
                className={`segmented__option ${publishMode === "NOW" ? "is-selected" : ""}`}
                onClick={() => {
                  if (scheduleOnly) return;
                  setPublishMode("NOW");
                  reset();
                }}
                aria-pressed={publishMode === "NOW"}
                disabled={publishing || scheduleOnly}
                title={
                  scheduleOnly
                    ? `A ${postTypeSpec?.label} can only be scheduled.`
                    : undefined
                }
              >
                <IconBolt />
                Publish now
              </button>
              <button
                type="button"
                className={`segmented__option ${publishMode === "SCHEDULE" ? "is-selected" : ""}`}
                onClick={() => {
                  setPublishMode("SCHEDULE");
                  reset();
                }}
                aria-pressed={publishMode === "SCHEDULE"}
                disabled={publishing}
              >
                <IconCalendar />
                Schedule
              </button>
            </div>

            {scheduleOnly && (
              <p className="publish__note">
                A {postTypeSpec?.label} is always scheduled, so it cannot be shared immediately.
              </p>
            )}

            {publishMode === "SCHEDULE" && (
              <div className="publish__schedule">
                <SchedulePicker
                  date={date}
                  time={time}
                  timezone={timezone}
                  minDate={minDate}
                  disabled={publishing}
                  error={
                    fieldError("scheduled_at") ??
                    (error && ["INVALID_SCHEDULE", "SCHEDULE_IN_PAST"].includes(error.code)
                      ? error.message
                      : null) ??
                    (date && time && !scheduleValid
                      ? "Please choose a future date and time."
                      : null)
                  }
                  onChange={({ date: d, time: t, timezone: tz }) => {
                    setDate(d);
                    setTime(t);
                    setTimezone(tz);
                    reset();
                  }}
                />
                <p className="publish__note">
                  Scheduled posts are handed to Buffer with a due time; Buffer publishes them.
                  This app has no scheduler of its own and does not re-check whether they went
                  out.
                </p>
              </div>
            )}
          </Card>
        </div>

        <div className="publish-col publish-col--side">
          <div className="publish-sticky">
            <div className="publish__preview-head">
              <h2 className="publish__section-title">Preview</h2>
              <span className="publish__preview-note">Exactly what gets sent</span>
            </div>

            <PostPreview
              accountName={account?.name ?? null}
              channel={channel}
              content={content}
              media={media}
              publishMode={publishMode}
              postType={postType ?? capability?.default_post_type ?? null}
              date={date}
              time={time}
              timezone={timezone}
            />

            <div className="publish__actions">
              <Button
                variant="primary"
                size="lg"
                fullWidth
                loading={publishing}
                disabled={!canSubmit}
                onClick={submit}
              >
                {publishing
                  ? "Publishing…"
                  : publishMode === "NOW"
                    ? "Publish"
                    : "Schedule post"}
              </Button>

              <Button
                size="lg"
                fullWidth
                disabled={!accountId || !channelId || !contentValid || publishing}
                onClick={addToDraft}
              >
                Add to campaign draft
              </Button>

              {!canSubmit && !publishing && (
                <p className="publish__blocker" role="status">
                  {blockerReason({
                    accountId,
                    channelId,
                    contentValid,
                    mediaValid,
                    mediaCountValid,
                    maxAssets,
                    mediaCount: media.length,
                    textValid,
                    scheduleValid,
                    requiredMedia,
                    minAssets,
                    postTypeAllowsVideo: Boolean(postTypeSpec?.allows_video),
                    postTypeAllowsImage: Boolean(postTypeSpec?.allows_image),
                    publishMode,
                    channelUsable,
                    channelBlockReason,
                    postTypeSupported,
                    scheduleOnly,
                    postTypeLabel: postTypeSpec?.label ?? null,
                  })}
                </p>
              )}
            </div>

            <p className="publish__footnote">
              Publishing goes through your configured engine and Buffer.
            </p>

            {(drafts.length > 0 || draftSaved) && (
              <Card className="draft-panel" padded>
                <h3 className="draft-panel__title">Campaign draft</h3>
                <p className="muted">
                  {drafts.length
                    ? `${drafts.length} channel${drafts.length === 1 ? "" : "s"} ready. Name it and save, then find it in Campaigns to add media or edit.`
                    : "Saved. Open it in Campaigns to edit or add media."}
                </p>

                {draftError && <p className="notice notice--error">{draftError}</p>}

                {draftSaved && !drafts.length && (
                  <p className="notice notice--ok">
                    Saved to Campaigns.{" "}
                    <Link to="/campaigns">Open campaigns</Link>
                  </p>
                )}

                <ul className="draft-list">
                  {drafts.map((row) => {
                    const info = platformMeta(
                      channels.find((c) => c.id === row.channel_id)?.platform,
                    );
                    return (
                      <li key={row.key} className="draft-list__row">
                        <div className="draft-list__meta">
                          <Badge tone="info">{info.label}</Badge>
                          <span className="muted">{row.content.slice(0, 60)}</span>
                          {row.post_type && (
                            <Badge tone="neutral">{row.post_type.replace(/_/g, " ")}</Badge>
                          )}
                          {row.media.length > 0 && (
                            <span className="muted">{row.media.length} media</span>
                          )}
                        </div>
                        <div className="row-actions">
                          <Button size="sm" onClick={() => loadDraftIntoComposer(row)}>
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() =>
                              setDrafts((rows) => rows.filter((r) => r.key !== row.key))
                            }
                          >
                            Remove
                          </Button>
                        </div>
                      </li>
                    );
                  })}
                </ul>

                {drafts.length > 0 && (
                  <div className="draft-panel__save">
                    <Field label="Campaign name" htmlFor="draft-name">
                      <input
                        id="draft-name"
                        className="input"
                        value={draftName}
                        onChange={(e) => setDraftName(e.target.value)}
                        placeholder="October product launch"
                      />
                    </Field>
                    <Button
                      variant="primary"
                      fullWidth
                      loading={savingDraft}
                      onClick={saveDraftCampaign}
                    >
                      Save as campaign draft
                    </Button>
                  </div>
                )}
              </Card>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** The platform's own limit, narrowed by the chosen post type when it has one. */
function resolveMaxAssets(
  capability: ReturnType<typeof usePlatformCapability>["capability"],
  postType: string | null,
): number | null {
  if (!capability) return null;
  if (postType) {
    const spec = capability.post_types.find((t) => t.key === postType);
    if (spec && spec.max_assets !== null) return spec.max_assets;
  }
  return capability.max_assets;
}

function blockerReason(s: {
  accountId: string | null;
  channelId: string | null;
  contentValid: boolean;
  mediaValid: boolean;
  mediaCountValid: boolean;
  mediaCount: number;
  maxAssets: number | null;
  textValid: boolean;
  scheduleValid: boolean;
  requiredMedia: boolean;
  minAssets: number;
  postTypeAllowsVideo: boolean;
  postTypeAllowsImage: boolean;
  publishMode: PublishMode;
  channelUsable: boolean;
  channelBlockReason: string | null;
  postTypeSupported: boolean;
  scheduleOnly: boolean;
  postTypeLabel: string | null;
}): string | null {
  if (!s.accountId) return "Choose an account to continue.";
  if (!s.channelId) return "Choose a channel to continue.";
  if (!s.channelUsable) return s.channelBlockReason ?? "This channel cannot publish right now.";
  if (!s.postTypeSupported) return "Choose a format this channel supports.";
  if (!s.contentValid) return "Write something to publish.";
  if (!s.textValid) return "Shorten the post to fit this channel's character limit.";
  if (!s.mediaValid)
    return s.requiredMedia
      ? s.minAssets > 1
        ? `This format needs at least ${s.minAssets} attachments. You have ${s.mediaCount}.`
        : `${s.postTypeLabel ?? "This format"} needs ${
            s.postTypeAllowsVideo && !s.postTypeAllowsImage
              ? "a video"
              : s.postTypeLabel === "Story" || s.postTypeLabel === "Reel"
                ? "a video"
                : "an image or video"
          } before it can be published.`
      : null;
  if (!s.mediaCountValid)
    return s.maxAssets === null
      ? null
      : `This format takes at most ${s.maxAssets} attachment${s.maxAssets === 1 ? "" : "s"}. You have ${s.mediaCount}.`;
  if (s.scheduleOnly && s.publishMode === "NOW")
    return `A ${s.postTypeLabel} can only be scheduled.`;
  if (!s.scheduleValid) return "Choose a future date and time.";
  return null;
}

function IconBolt() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2 4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5Z" />
    </svg>
  );
}

function IconCalendar() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="16" rx="2.5" />
      <path d="M8 3v4M16 3v4M3 10h18" />
    </svg>
  );
}