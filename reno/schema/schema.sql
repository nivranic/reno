CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS video_asset (
  video_id TEXT PRIMARY KEY,
  source_platform TEXT,
  source_type TEXT,
  source_url TEXT,
  title TEXT,
  author TEXT,
  source_published_at TEXT,
  duration_ms INTEGER,
  width INTEGER,
  height INTEGER,
  fps REAL,
  content_sha256 TEXT UNIQUE,
  audio_sha256 TEXT,
  imported_at TEXT,
  files_json TEXT,
  status TEXT DEFAULT 'imported',
  sync_state TEXT NOT NULL DEFAULT 'ready'
);

CREATE TABLE IF NOT EXISTS frame (
  frame_id TEXT NOT NULL,
  video_id TEXT,
  pts_ms INTEGER,
  origin TEXT,
  dhash TEXT,
  path TEXT,
  PRIMARY KEY (video_id, frame_id)
);

CREATE TABLE IF NOT EXISTS transcript_segment (
  id TEXT NOT NULL,
  video_id TEXT,
  idx INTEGER,
  start_ms INTEGER,
  end_ms INTEGER,
  text TEXT,
  PRIMARY KEY (video_id, id)
);

CREATE TABLE IF NOT EXISTS ocr_span (
  id TEXT NOT NULL,
  video_id TEXT,
  start_ms INTEGER,
  end_ms INTEGER,
  text TEXT,
  conf REAL,
  frame_ids_json TEXT,
  PRIMARY KEY (video_id, id)
);

CREATE TABLE IF NOT EXISTS visual_observation (
  id TEXT NOT NULL,
  video_id TEXT,
  frame_id TEXT,
  pts_ms INTEGER,
  scene_description TEXT,
  materials_json TEXT,
  tools_json TEXT,
  subtitle_text TEXT,
  measurement TEXT,
  PRIMARY KEY (video_id, id)
);

CREATE TABLE IF NOT EXISTS knowledge_atom (
  id TEXT PRIMARY KEY,
  video_id TEXT,
  category TEXT,
  stage TEXT,
  space TEXT,
  subject TEXT,
  claim TEXT,
  reason TEXT,
  risk_if_ignored TEXT,
  polarity TEXT,
  conditions_json TEXT,
  parameters_json TEXT,
  confidence REAL,
  status TEXT DEFAULT 'candidate',
  cluster_id TEXT,
  model TEXT,
  prompt_version TEXT,
  created_at TEXT,
  dimension TEXT,
  evidence_nature TEXT,
  exceptions TEXT,
  prices_json TEXT
);

CREATE TABLE IF NOT EXISTS evidence_ref (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  atom_id TEXT,
  video_id TEXT,
  modality TEXT,
  source_item_id TEXT,
  start_ms INTEGER,
  end_ms INTEGER,
  evidence_text TEXT,
  weight REAL
);

CREATE TABLE IF NOT EXISTS knowledge_cluster (
  cluster_id TEXT PRIMARY KEY,
  canonical_topic TEXT,
  relation TEXT,
  judge_reason TEXT,
  members_json TEXT,
  linked_conflict TEXT,
  created_at TEXT,
  derivation_json TEXT
);

CREATE TABLE IF NOT EXISTS conflict_case (
  conflict_id TEXT PRIMARY KEY,
  ctype TEXT,
  status TEXT,
  side_a_json TEXT,
  side_b_json TEXT,
  analysis_json TEXT,
  recommended_action TEXT,
  created_at TEXT,
  derivation_json TEXT
);

CREATE TABLE IF NOT EXISTS user_decision (
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  atom_id TEXT,
  conflict_id TEXT,
  action TEXT,
  revised_claim TEXT,
  note TEXT,
  created_at TEXT,
  event_id TEXT,
  client_id TEXT,
  decided_at TEXT,
  target_fingerprint TEXT,
  target_snapshot_json TEXT,
  event_schema_version INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_event ON user_decision(event_id);

CREATE TABLE IF NOT EXISTS processing_run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id TEXT,
  step TEXT,
  model TEXT,
  prompt_version TEXT,
  schema_version TEXT,
  taxonomy_version TEXT,
  started_at TEXT,
  completed_at TEXT,
  ok INTEGER,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS job (
  job_id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id TEXT,
  kind TEXT,
  status TEXT DEFAULT 'pending',
  attempts INTEGER DEFAULT 0,
  error TEXT,
  created_at TEXT,
  updated_at TEXT,
  request_id TEXT,
  scope_key TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  owner_id TEXT,
  lease_until INTEGER,
  result_json TEXT,
  started_at TEXT,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_seg_video ON transcript_segment(video_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_ocr_video ON ocr_span(video_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_vis_video ON visual_observation(video_id, pts_ms);
CREATE INDEX IF NOT EXISTS idx_atom_video ON knowledge_atom(video_id);
CREATE INDEX IF NOT EXISTS idx_ev_atom ON evidence_ref(atom_id);
CREATE INDEX IF NOT EXISTS idx_job_status ON job(status);

-- cloud-role import queue: /api/import enqueues URLs here on a cloud node,
-- the home sync agent claims them (atomic UPDATE) with a lease; see
-- docs/sync-plan.md section 4.1
CREATE TABLE IF NOT EXISTS sync_queue (
  item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  status TEXT DEFAULT 'queued',
  attempts INTEGER DEFAULT 0,
  lease_until TEXT,
  last_error_class TEXT,
  created_at TEXT,
  updated_at TEXT,
  owner_id TEXT,
  owner_generation INTEGER NOT NULL DEFAULT 0,
  lease_token TEXT,
  lease_generation INTEGER NOT NULL DEFAULT 0,
  retry_at TEXT,
  video_id TEXT,
  publication_id TEXT,
  result_hash TEXT,
  budget_wait_reason TEXT,
  budget_operation_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_syncq_status ON sync_queue(status);

CREATE VIRTUAL TABLE IF NOT EXISTS atom_fts USING fts5(
  atom_id UNINDEXED, claim, reason, category UNINDEXED, space UNINDEXED
);

-- Knowledge revision is transactional and covers maintenance scripts issuing
-- SQL directly. Identical updates and diagnostic timestamps do not invalidate.
CREATE TRIGGER IF NOT EXISTS kb_source_update AFTER UPDATE ON video_asset
WHEN OLD.source_published_at IS NOT NEW.source_published_at
  OR OLD.source_url IS NOT NEW.source_url OR OLD.title IS NOT NEW.title
  OR OLD.author IS NOT NEW.author
BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_atom_insert AFTER INSERT ON knowledge_atom BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_atom_delete AFTER DELETE ON knowledge_atom BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_atom_update AFTER UPDATE ON knowledge_atom
WHEN OLD.id IS NOT NEW.id OR OLD.video_id IS NOT NEW.video_id
  OR OLD.category IS NOT NEW.category OR OLD.stage IS NOT NEW.stage
  OR OLD.space IS NOT NEW.space OR OLD.subject IS NOT NEW.subject
  OR OLD.claim IS NOT NEW.claim OR OLD.reason IS NOT NEW.reason
  OR OLD.risk_if_ignored IS NOT NEW.risk_if_ignored OR OLD.polarity IS NOT NEW.polarity
  OR OLD.conditions_json IS NOT NEW.conditions_json OR OLD.parameters_json IS NOT NEW.parameters_json
  OR OLD.confidence IS NOT NEW.confidence OR OLD.status IS NOT NEW.status
  OR OLD.cluster_id IS NOT NEW.cluster_id OR OLD.dimension IS NOT NEW.dimension
  OR OLD.evidence_nature IS NOT NEW.evidence_nature OR OLD.exceptions IS NOT NEW.exceptions
  OR OLD.prices_json IS NOT NEW.prices_json
BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_evidence_insert AFTER INSERT ON evidence_ref BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_evidence_delete AFTER DELETE ON evidence_ref BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_evidence_update AFTER UPDATE ON evidence_ref
WHEN OLD.atom_id IS NOT NEW.atom_id OR OLD.video_id IS NOT NEW.video_id
  OR OLD.modality IS NOT NEW.modality OR OLD.source_item_id IS NOT NEW.source_item_id
  OR OLD.start_ms IS NOT NEW.start_ms OR OLD.end_ms IS NOT NEW.end_ms
  OR OLD.evidence_text IS NOT NEW.evidence_text OR OLD.weight IS NOT NEW.weight
BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_cluster_insert AFTER INSERT ON knowledge_cluster BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_cluster_delete AFTER DELETE ON knowledge_cluster BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_cluster_update AFTER UPDATE ON knowledge_cluster
WHEN OLD.cluster_id IS NOT NEW.cluster_id OR OLD.canonical_topic IS NOT NEW.canonical_topic
  OR OLD.relation IS NOT NEW.relation OR OLD.judge_reason IS NOT NEW.judge_reason
  OR OLD.members_json IS NOT NEW.members_json OR OLD.linked_conflict IS NOT NEW.linked_conflict
  OR OLD.derivation_json IS NOT NEW.derivation_json
BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_conflict_insert AFTER INSERT ON conflict_case BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_conflict_delete AFTER DELETE ON conflict_case BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;
CREATE TRIGGER IF NOT EXISTS kb_conflict_update AFTER UPDATE ON conflict_case
WHEN OLD.conflict_id IS NOT NEW.conflict_id OR OLD.ctype IS NOT NEW.ctype
  OR OLD.status IS NOT NEW.status OR OLD.side_a_json IS NOT NEW.side_a_json
  OR OLD.side_b_json IS NOT NEW.side_b_json OR OLD.analysis_json IS NOT NEW.analysis_json
  OR OLD.recommended_action IS NOT NEW.recommended_action
  OR OLD.derivation_json IS NOT NEW.derivation_json
BEGIN
  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';
END;

-- Task dispatch: durable request receipts, scope uniqueness and worker lease.
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_request ON job(request_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_active_scope ON job(scope_key)
  WHERE scope_key IS NOT NULL AND status IN ('pending','running');
CREATE TABLE IF NOT EXISTS task_request (
  request_id TEXT PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES job(job_id),
  payload_signature TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_lease (
  scope TEXT PRIMARY KEY,
  owner_id TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);
