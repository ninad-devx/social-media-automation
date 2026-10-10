/** Shapes returned by /api/ai/*. Mirrors backend/app/schemas/ai.py. */

export interface ProviderCatalogueItem {
  slug: string;
  display_name: string;
  default_base_url: string;
  default_model: string;
  supports_model_discovery: boolean;
  requires_base_url: boolean;
  key_label: string;
  help_url: string;
}

export interface AiProvider {
  id: string;
  provider_slug: string;
  provider_name: string;
  display_name: string;
  base_url: string;
  model: string;
  organization: string | null;
  project: string | null;
  enabled: boolean;
  is_default: boolean;
  supports_model_discovery: boolean;
  requires_base_url: boolean;
  default_base_url: string;
  default_model: string;
  key_label: string;
  help_url: string;
  has_api_key: boolean;
  /** Display mask only, e.g. "sk-t...7654". Never the real key. */
  api_key_masked: string | null;
  last_status: string | null;
  last_error_code: string | null;
  last_tested_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  encryption_available: boolean;
}

export interface ProviderPayload {
  provider_slug: string;
  display_name: string;
  api_key?: string;
  base_url?: string | null;
  model?: string | null;
  organization?: string | null;
  project?: string | null;
  enabled?: boolean;
  make_default?: boolean;
}

export interface ProviderTestResult {
  ok: boolean;
  detail: string | null;
  error_code: string | null;
  tested_at: string | null;
}

export interface ModelList {
  ok: boolean;
  models: string[];
  detail: string | null;
  error_code: string | null;
  manual_entry_allowed: boolean;
}

/**
 * Result of probing a pasted key before it is saved.
 * The key is used for one live check and discarded; it is never stored here.
 */
export interface DetectProviderResult {
  detected_slug: string;
  confident: boolean;
  reason: string;
  display_name: string;
  default_base_url: string;
  default_model: string | null;
  supports_model_discovery: boolean;
  requires_base_url: boolean;
  key_label: string;
  help_url: string | null;
  credentials_ok: boolean;
  detail: string | null;
  error_code: string | null;
  models: string[];
}

/* ---- instructions ---- */

export interface GlobalInstructions {
  content: string;
  updated_at: string | null;
}

export interface AccountInstructions {
  account_id: string;
  account_name: string;
  description: string | null;
  industry: string | null;
  target_audience: string | null;
  products: string | null;
  voice: string | null;
  goals: string | null;
  preferred_cta: string | null;
  terminology: string | null;
  avoid_words: string | null;
  hashtag_preferences: string | null;
  language: string | null;
  extra_context: string | null;
  instructions: string | null;
  configured: boolean;
  updated_at: string | null;
}

export interface ChannelInstructions {
  channel_id: string;
  account_id: string;
  account_name: string;
  platform: string;
  platform_label: string;
  display_name: string;
  instructions: string | null;
  configured: boolean;
  updated_at: string | null;
}

export interface InstructionLayer {
  key: string;
  label: string;
  rank: number;
  configured: boolean;
  detail: string;
}

export interface EffectiveInstructions {
  channel_id: string;
  account_id: string;
  platform: string;
  platform_label: string;
  layers: InstructionLayer[];
  text: string;
  system_rules_preview: string;
}

export interface TestGenerationResult {
  ok: boolean;
  post_text?: string;
  hashtags?: string[];
  model?: string;
  error_code?: string;
  message?: string;
}

/* ---- campaigns ---- */

export interface CampaignTarget {
  account_id: string;
  channel_id: string;
}

export interface CampaignDraft {
  account_id: string;
  channel_id: string;
  content: string;
  media?: Array<{ type?: string; url?: string; alt_text?: string | null }>;
  hashtags?: string[];
}

export interface CampaignPayload {
  name?: string | null;
  topic: string;
  objective?: string | null;
  context?: string | null;
  audience_override?: string | null;
  tone_override?: string | null;
  language?: string;
  campaign_instructions?: string | null;
  cta?: string | null;
  keywords?: string | null;
  avoid_claims?: string | null;
  /** Create an image for every post, not just platforms that require media. */
  generate_images?: boolean;
  /** Hand-written per-channel posts, used by Quick Publish drafts. */
  drafts?: CampaignDraft[];
  targets: CampaignTarget[];
}

export interface CampaignPost {
  id: string;
  campaign_id: string;
  account_id: string;
  channel_id: string;
  platform: string;
  platform_label: string;
  account_name: string;
  generation_status: string;
  publishing_status: string;
  generated_content: string | null;
  edited_content: string | null;
  effective_content: string;
  hashtags: string[];
  media_idea: string | null;
  media: Array<{ type?: string; url?: string; alt_text?: string }>;
  model_notes: string | null;
  validation_warnings: string[];
  is_edited: boolean;
  has_previous_version: boolean;
  scheduled_at: string | null;
  timezone: string | null;
  provider_id: string | null;
  model: string | null;
  error_code: string | null;
  error_message: string | null;
  publication_id: string | null;
  external_link: string | null;
  character_count: number;
  character_limit: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Campaign {
  id: string;
  name: string;
  topic: string;
  objective: string | null;
  generate_images: boolean;
  /** "ai_campaign" or "quick_publish". */
  source: string;
  status: string;
  language: string;
  provider_id: string | null;
  model: string | null;
  target_count: number;
  generated_count: number;
  failed_count: number;
  published_count: number;
  scheduled_count: number;
  created_at: string | null;
  updated_at: string | null;
  posts: CampaignPost[];
}

export interface CampaignPage {
  items: Campaign[];
  total: number;
  limit: number;
  offset: number;
}

export interface GenerationStatus {
  campaign_id: string;
  status: string;
  pending: number;
  generating: number;
  generated: number;
  failed: number;
  skipped: number;
  published: number;
  scheduled: number;
  total: number;
}

export interface PostActionResult {
  post_id: string;
  account_id: string;
  channel_id: string;
  ok: boolean;
  status: string | null;
  publication_id: string | null;
  external_link: string | null;
  scheduled_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface BulkResult {
  requested: number;
  succeeded: number;
  failed: number;
  results: PostActionResult[];
}