/** Typed mirrors of the FastAPI /api responses (app/api.py is the source of
 * truth). Boundary conversions live in lib/api.ts, not in components. */

export type Modality = "ASR" | "OCR" | "VIS";

export type DerivationState = "active" | "stale" | "missing" | "unknown";
export interface Derivation {
  state: DerivationState;
  reason: string;
  changed_count: number;
  missing_count: number;
}
export type DerivationCounts = Record<DerivationState, number> & { total?: number };
export interface DerivationSummary { clusters: DerivationCounts; conflicts: DerivationCounts }

export interface VideoRow {
  video_id: string;
  title: string | null;
  author: string | null;
  status: string;
  duration_ms: number | null;
  imported_at: string | null;
  source_platform: string | null;
  atoms: number;
  error_detail?: string | null;
}

export interface VideosResponse {
  videos: VideoRow[];
  stats: {
    videos: number;
    atoms: number;
    clusters: number;
    conflicts: number;
    conflicts_open: number;
    jobs: Record<string, number>;
  };
}

export interface VideoMeta {
  video_id: string;
  title: string | null;
  author: string | null;
  status: string;
  duration_ms: number | null;
  imported_at: string | null;
  source_platform: string | null;
  source_url: string | null;
  has_media: boolean;
  atoms: number;
}

export interface TimelineEvent {
  id: string;
  ms: number;
  end: number;
  mod: Modality;
  text: string;
}

export interface AtomParameter {
  name: string;
  value: string | number;
  unit?: string | null;
  param_status?: string;
}

export interface AtomEvidence {
  id: string;
  ms: number;
  end: number;
  mod: string; // backend may emit lowercase modality on atoms; normalized on load
  text: string;
}

export interface WorkbenchAtom {
  condition_match?: ConditionMatch;
  id: string;
  category: string | null;
  space: string | null;
  claim: string;
  polarity: string;
  confidence: number;
  status: string;
  cluster_id: string | null;
  stage?: string | null;
  dimension?: string | null;
  evidence_nature?: string | null;
  exceptions?: string[];
  conditions?: Record<string, unknown>;
  prices?: AtomPrice[];
  review_status?: string;
  review_stale?: boolean;
  has_conflict?: boolean;
  has_outdated_conflict?: boolean;
  source_published_at?: string | null;
  target_fingerprint?: string;
  parameters: AtomParameter[];
  evidence: AtomEvidence[];
}

export interface EventsResponse {
  events: TimelineEvent[];
  atoms: WorkbenchAtom[];
}

export interface ConflictSide {
  atoms: string[];
  claim: string;
  conditions?: { space?: string } | null;
  video: string;
}

export interface Conflict {
  derivation?: Derivation;
  target_fingerprint?: string;
  review_stale?: boolean;
  conflict_id: string;
  ctype: string;
  status: string; // needs_review | needs_user_decision | decided:<action>
  side_a: ConflictSide;
  side_b: ConflictSide;
  analysis: {
    conditional_conclusion?: {
      condition_overlap: boolean;
      scope_split: string;
      authority_gap: string;
    } | null;
    judge_note?: string | null;
  };
  recommended_action: string | null;
  created_at: string;
}

export interface ConflictsResponse {
  conflicts: Conflict[];
}

export interface AtomPrice {
  object: string;
  brand?: string;
  model?: string;
  spec?: string;
  region?: string;
  channel?: string;
  amount: number;
  unit?: string;
  basis?: string;
  price_kind: string | null; // null = 未注明口径 (never coerced)
  includes?: string[];
  valid_at?: string;
  note?: string;
}

export interface SearchRow {
  has_conflict?: boolean;
  has_outdated_conflict?: boolean;
  condition_match?: ConditionMatch;
  id: string;
  claim: string;
  subject: string | null;
  category: string | null;
  space: string | null;
  stage: string | null;
  dimension: string | null;
  evidence_nature: string | null;
  polarity: string;
  status: string;
  conditions: Record<string, unknown>;
  parameters: AtomParameter[];
  prices: AtomPrice[];
  exceptions?: string[];
  review_status?: string;
  source_published_at?: string | null;
  target_fingerprint?: string;
  video: string | null;
  video_title: string;
  ms: number;
  mod: string | null;
  evidence_text: string;
}

export interface SearchResponse {
  results: SearchRow[];
  page?: number;
  page_size?: number;
  total?: number;
  has_more?: boolean;
  kb_revision?: number;
}

export interface CompareSide {
  item: string;
  total: number;
  groups: { dimension: string; atoms: SearchRow[]; total?: number; truncated?: boolean }[];
  missing_dimensions: string[];
  coverage_scope?: "returned_results" | "full_filtered_set";
  truncated?: boolean;
}

export interface CompareResponse {
  sides: CompareSide[];
  conflicts: {
    derivation?: Derivation;
    cluster_id: string;
    topic: string | null;
    linked_conflict: string | null;
    items: string[];
  }[];
  dimensions: string[];
  kb_revision?: number;
  context_used?: Record<string, unknown>;
  context_unknown?: string[];
  context_signature?: string;
  per_dimension_limit?: number;
}

export interface AskHistoryTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ConditionMatch {
  status: "match" | "mismatch" | "unknown";
  reason: string;
  details: { key: string; requested: unknown; source: unknown; status: "match" | "mismatch" | "unknown"; reason: string }[];
  exceptions_evaluated: boolean;
}

export interface AskRef {
  has_conflict?: boolean;
  has_outdated_conflict?: boolean;
  condition_match?: ConditionMatch;
  n: number;
  atom_id: string;
  claim: string;
  video: string | null;
  video_title: string;
  ms: number;
  modality: string | null;
  polarity: string | null;
  stage: string | null;
  category: string | null;
  space: string | null;
  subject: string | null;
  dimension: string | null;
  evidence_nature: string | null;
  conditions: Record<string, unknown>;
  parameters: AtomParameter[];
  prices: AtomPrice[];
  exceptions?: string[];
  review_status?: string;
  source_published_at?: string | null;
  target_fingerprint?: string;
}

export interface AskConflictInfo {
  derivation?: Derivation;
  cluster_id: string;
  topic: string;
  conflict_id: string | null;
}

export interface AskResponse {
  answer: string;
  refs: AskRef[];
  conflicts: AskConflictInfo[];
  degraded?: boolean;
  generated_at?: string;
  kb_revision?: number;
  request_id?: string;
  context_used?: Record<string, unknown>;
  context_unknown?: string[];
  context_signature?: string;
  retrieval_scope?: string;
}

export interface FacetsResponse {
  categories: string[];
  spaces: string[];
  stages: string[];
  dimensions: string[];
}

export interface ReportsResponse {
  reports: Record<string, string>;
}

export interface HealthResponse {
  ok: boolean;
  cookies_configured: boolean;
  vlm_enabled: boolean;
  atomize_model: string;
  model_presets: string[];
  node_role?: string;
  library_id?: string;
}

export interface ImportResult {
  status: string; // imported | duplicate | ...
  video_id?: string;
  task_id?: number;
  request_id?: string;
  node_role?: "all" | "home" | "cloud";
}

export type ImportBatchResult = {
  imported: number;
  duplicate: number;
  failed: number;
  failed_sample: string[];
} | {
  status?: "queued";
  queued: number;
  task_ids?: number[];
  request_id?: string;
  node_role?: "all" | "home" | "cloud";
  skipped?: number;
};

export type DecisionAction = "accept_a" | "accept_b" | "both" | "reject";
