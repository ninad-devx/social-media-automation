import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DateTime } from "luxon";
import { Card, Button, Field, Badge } from "../components/ui";
import { AccountChannelSelectors } from "../components/quick-publish/AccountChannelSelectors";
import { MediaPicker } from "../components/quick-publish/MediaPicker";
import { SchedulePicker, buildScheduledAt, isFuture } from "../components/quick-publish/SchedulePicker";
import { PostPreview } from "../components/quick-publish/PostPreview";
import { PublishError, PublishSuccess } from "../components/quick-publish/PublishFeedback";
import { useAccounts } from "../hooks/useAccounts";
import { useChannels } from "../hooks/useChannels";
import { usePublish } from "../hooks/usePublish";
import { campaignsApi } from "../services/ai";
import { platformMeta } from "../lib/platforms";
import type { MediaItem, PublishMode } from "../types/publishing";

const DEFAULT_TIMEZONE = import.meta.env.VITE_DEFAULT_TIMEZONE ?? "Asia/Dhaka";

/** One composed post waiting to be saved into a named campaign. */
interface DraftRow {
  key: string;
  account_id: string;
  channel_id: string;
  content: string;
  media: Array<{ type?: string; url?: string; alt_text?: string | null }>;
}

export function QuickPublishPage() {
  const { data: accounts } = useAccounts();

  const [accountId, setAccountId] = useState<string | null>(null);
  const [channelId, setChannelId] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [media, setMedia] = useState<MediaItem[]>([]);
  const [publishMode, setPublishMode] = useState<PublishMode>("NOW");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [timezone, setTimezone] = useState(DEFAULT_TIMEZONE);

  const { channels } = useChannels(accountId);
  const { publishing, result, error, publish, reset } = usePublish();

  // Preselect the first account once, so the page is usable immediately.
  useEffect(() => {
    if (!accountId && accounts?.length) setAccountId(accounts[0].id);
  }, [accounts, accountId]);

  // Changing account invalidates the channel: refetch and clear.
  const onAccountChange = (id: string) => {
    setAccountId(id);
    setChannelId(null);
    reset();
  };

  const channel = useMemo(
    () => channels.find((c) => c.id === channelId) ?? null,
    [channels, channelId],
  );
  const account = useMemo(
    () => accounts?.find((a) => a.id === accountId) ?? null,
    [accounts, accountId],
  );

  const meta = platformMeta(channel?.platform);
  const trimmedLength = content.trim().length;

  // Default a future date/time the moment scheduling is chosen.
  useEffect(() => {
    if (publishMode !== "SCHEDULE") return;
    if (date && time) return;
    const soon = DateTime.now().plus({ hours: 2 }).setZone(timezone);
    if (soon.minute < 30) soon.plus({ minutes: 30 - soon.minute }).startOf("hour");
    else soon.plus({ minutes: 5 }).startOf("minute");
    setDate(soon.toFormat("yyyy-MM-dd"));
    setTime(soon.toFormat("HH:mm"));
  }, [publishMode, date, time, timezone]);

  const minDate = DateTime.now().toFormat("yyyy-MM-dd");

  // ---- client-side gating, mirroring the backend's rules ---------------
  const scheduleValid = publishMode === "NOW" || isFuture(date, time, timezone);
  const contentValid = trimmedLength > 0;
  const mediaValid = !meta.requiresMedia || media.length > 0;
  const canSubmit =
    Boolean(accountId) && Boolean(channelId) && contentValid && mediaValid && scheduleValid && !publishing;

  const fieldError = (field: string) =>
    error?.field === field ? error.message : null;

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
    });
  };

  const onResultDismiss = () => {
    reset();
    setContent("");
    setMedia([]);
  };

  /* ---- draft campaign ------------------------------------------------- */
  // Compose a post per channel, then name the campaign and save it. It shows
  // up in Campaigns where it can be edited and given media before publishing.
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
        },
      ];
    });
    // Clear the composer so the next channel starts fresh.
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

  const overLimit = meta.suggestedLimit !== null && trimmedLength > meta.suggestedLimit;

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
              onAccountChange={onAccountChange}
              onChannelChange={(id) => {
                setChannelId(id);
                reset();
              }}
            />
            {channels.length === 0 && accountId && (
              <p className="publish__note">
                This account has no active channels yet. Add one in the backend to publish here.
              </p>
            )}
          </Card>

          <Card>
            <div className="publish__section-head">
              <h2 className="publish__section-title">Your post</h2>
              {channel && (
                <Badge tone="neutral">
                  {meta.label}
                  {meta.requiresMedia && " · media required"}
                </Badge>
              )}
            </div>

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
                    {meta.suggestedLimit !== null &&
                      ` / ${meta.suggestedLimit.toLocaleString()} characters`}
                  </span>
                </div>
              </div>
            </Field>

            <div className="publish__media">
              <span className="selector__label" id="media-label">
                Media
                {meta.requiresMedia && <span className="field__optional">Required</span>}
              </span>
              <MediaPicker
                media={media}
                onChange={setMedia}
                disabled={publishing}
                requiresMedia={meta.requiresMedia}
                error={fieldError("media") ?? (error?.code === "MEDIA_INVALID" ? error.message : null)}
              />
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
                  setPublishMode("NOW");
                  reset();
                }}
                aria-pressed={publishMode === "NOW"}
                disabled={publishing}
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
                <p className="publish__blocker">{blockerReason({
                  accountId, channelId, contentValid, mediaValid, scheduleValid,
                  requiresMedia: meta.requiresMedia, publishMode,
                })}</p>
              )}
            </div>

            <p className="publish__footnote">
              Publishing runs through your configured n8n workflow and Buffer.
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
                      channels.find((c) => c.id === row.channel_id)?.platform
                    );
                    return (
                      <li key={row.key} className="draft-list__row">
                        <div className="draft-list__meta">
                          <Badge tone="info">{info.label}</Badge>
                          <span className="muted">{row.content.slice(0, 60)}</span>
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

function blockerReason(s: {
  accountId: string | null;
  channelId: string | null;
  contentValid: boolean;
  mediaValid: boolean;
  scheduleValid: boolean;
  requiresMedia: boolean;
  publishMode: PublishMode;
}): string | null {
  if (!s.accountId) return "Choose an account to continue.";
  if (!s.channelId) return "Choose a channel to continue.";
  if (!s.contentValid) return "Write something to publish.";
  if (!s.mediaValid)
    return s.requiresMedia
      ? "This channel needs at least one image or video."
      : "";
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