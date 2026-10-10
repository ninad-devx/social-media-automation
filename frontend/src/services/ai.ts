import { api } from "./api";
import type { MediaCaps } from "../components/ai/PostPreviewCard";
import type {
  AccountInstructions,
  AiProvider,
  BulkResult,
  Campaign,
  CampaignPage,
  CampaignPayload,
  CampaignPost,
  ChannelInstructions,
  DetectProviderResult,
  EffectiveInstructions,
  GenerationStatus,
  GlobalInstructions,
  ModelList,
  PostActionResult,
  ProviderCatalogueItem,
  ProviderPayload,
  ProviderTestResult,
  TestGenerationResult,
} from "../types/ai";

/*
 * AI endpoints.
 *
 * Provider API keys are write-only: they are sent once on create/update and
 * are never returned. Nothing here ever puts a key into browser storage.
 */
export const providersApi = {
  catalogue: (signal?: AbortSignal) =>
    api.get<ProviderCatalogueItem[]>("/api/ai/providers/catalogue", signal),
  list: (signal?: AbortSignal) => api.get<AiProvider[]>("/api/ai/providers", signal),
  create: (payload: ProviderPayload) => api.post<AiProvider>("/api/ai/providers", payload),
  update: (id: string, payload: Partial<ProviderPayload> & { make_default?: boolean }) =>
    api.patch<AiProvider>(`/api/ai/providers/${id}`, payload),
  remove: (id: string) => api.del<void>(`/api/ai/providers/${id}`),
  test: (id: string) => api.post<ProviderTestResult>(`/api/ai/providers/${id}/test`),
  models: (id: string, signal?: AbortSignal) =>
    api.get<ModelList>(`/api/ai/providers/${id}/models`, signal),

  /**
   * Probe a pasted key without saving it. Returns the detected provider plus
   * the models that key can actually use, so the model list is never empty
   * just because the provider was guessed wrong.
   */
  detect: (apiKey: string, providerSlug?: string, baseUrl?: string) =>
    api.post<DetectProviderResult>("/api/ai/providers/detect", {
      api_key: apiKey,
      provider_slug: providerSlug ?? null,
      base_url: baseUrl ?? null,
    }),
};

export const instructionsApi = {
  getGlobal: (signal?: AbortSignal) => api.get<GlobalInstructions>("/api/ai/instructions", signal),
  putGlobal: (content: string) => api.put<GlobalInstructions>("/api/ai/instructions", { content }),

  getAccount: (accountId: string, signal?: AbortSignal) =>
    api.get<AccountInstructions>(`/api/ai/accounts/${accountId}/instructions`, signal),
  putAccount: (accountId: string, payload: Partial<AccountInstructions>) =>
    api.put<AccountInstructions>(`/api/ai/accounts/${accountId}/instructions`, payload),

  getChannel: (channelId: string, signal?: AbortSignal) =>
    api.get<ChannelInstructions>(`/api/ai/channels/${channelId}/instructions`, signal),
  putChannel: (channelId: string, instructions: string) =>
    api.put<ChannelInstructions>(`/api/ai/channels/${channelId}/instructions`, { instructions }),
  resetChannel: (channelId: string) =>
    api.del<{ channel_id: string; reset: boolean }>(
      `/api/ai/channels/${channelId}/instructions`
    ),
  effective: (channelId: string, signal?: AbortSignal) =>
    api.get<EffectiveInstructions>(
      `/api/ai/channels/${channelId}/effective-instructions`,
      signal
    ),
  testGeneration: (channelId: string, topic: string) =>
    api.post<TestGenerationResult>(`/api/ai/channels/${channelId}/test-generation`, { topic }),
};

export const campaignsApi = {
  list: (params: { limit?: number; offset?: number; status?: string; source?: string } = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams();
    if (params.limit != null) query.set("limit", String(params.limit));
    if (params.offset != null) query.set("offset", String(params.offset));
    if (params.status) query.set("status", params.status);
    if (params.source) query.set("source", params.source);
    const suffix = query.toString() ? `?${query}` : "";
    return api.get<CampaignPage>(`/api/ai/campaigns${suffix}`, signal);
  },
  get: (id: string, signal?: AbortSignal) =>
    api.get<Campaign>(`/api/ai/campaigns/${id}`, signal),
  create: (payload: CampaignPayload) => api.post<Campaign>("/api/ai/campaigns", payload),
  update: (id: string, payload: Partial<CampaignPayload>) =>
    api.patch<Campaign>(`/api/ai/campaigns/${id}`, payload),
  remove: (id: string) => api.del<void>(`/api/ai/campaigns/${id}`),

  generate: (id: string, providerId?: string) =>
    api.post<Campaign>(`/api/ai/campaigns/${id}/generate`, {
      provider_id: providerId ?? null,
    }),
  generationStatus: (id: string, signal?: AbortSignal) =>
    api.get<GenerationStatus>(`/api/ai/campaigns/${id}/generation-status`, signal),

  regeneratePost: (postId: string, overwriteEdited = false) =>
    api.post<CampaignPost>(`/api/ai/campaign-posts/${postId}/regenerate`, {
      overwrite_edited: overwriteEdited,
    }),
  updatePost: (
    postId: string,
    payload: {
      content?: string;
      hashtags?: string[];
      media?: Array<{ type?: string; url?: string; alt_text?: string | null }>;
    }
  ) => api.patch<CampaignPost>(`/api/ai/campaign-posts/${postId}`, payload),
  restorePost: (postId: string) =>
    api.post<CampaignPost>(`/api/ai/campaign-posts/${postId}/restore`),
  generateImage: (postId: string) =>
    api.post<CampaignPost>(`/api/ai/campaign-posts/${postId}/generate-image`),
  discardEdits: (postId: string) =>
    api.post<CampaignPost>(`/api/ai/campaign-posts/${postId}/discard-edits`),
  removePost: (postId: string) => api.del<void>(`/api/ai/campaign-posts/${postId}`),

  publishPost: (postId: string) =>
    api.post<PostActionResult>(`/api/ai/campaign-posts/${postId}/publish`, {}),
  schedulePost: (postId: string, scheduledAt: string, timezone: string) =>
    api.post<PostActionResult>(`/api/ai/campaign-posts/${postId}/schedule`, {
      scheduled_at: scheduledAt,
      timezone,
    }),
  mediaCapabilities: (platform: string, signal?: AbortSignal) =>
    api.get<MediaCaps>(
      `/api/ai/media-capabilities?platform=${encodeURIComponent(platform)}`,
      signal
    ),

  bulkPublish: (postIds: string[]) =>
    api.post<BulkResult>("/api/ai/campaign-posts/bulk-publish", { post_ids: postIds }),
  bulkSchedule: (
    postIds: string[],
    scheduledAt: string,
    timezone: string,
    overrides: Record<string, { scheduled_at: string; timezone: string }> = {}
  ) =>
    api.post<BulkResult>("/api/ai/campaign-posts/bulk-schedule", {
      post_ids: postIds,
      scheduled_at: scheduledAt,
      timezone,
      overrides,
    }),
};