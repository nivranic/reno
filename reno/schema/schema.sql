PRAGMA journal_mode=WAL;

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
  duration_ms INTEGER,
  width INTEGER,
  height INTEGER,
  fps REAL,
  content_sha256 TEXT UNIQUE,
  audio_sha256 TEXT,
  imported_at TEXT,
  files_json TEXT,
  status TEXT DEFAULT 'imported'
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
  created_at TEXT
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
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS conflict_case (
  conflict_id TEXT PRIMARY KEY,
  ctype TEXT,
  status TEXT,
  side_a_json TEXT,
  side_b_json TEXT,
  analysis_json TEXT,
  recommended_action TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS user_decision (
  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
  atom_id TEXT,
  conflict_id TEXT,
  action TEXT,
  revised_claim TEXT,
  note TEXT,
  created_at TEXT
);

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
  updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_seg_video ON transcript_segment(video_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_ocr_video ON ocr_span(video_id, start_ms);
CREATE INDEX IF NOT EXISTS idx_vis_video ON visual_observation(video_id, pts_ms);
CREATE INDEX IF NOT EXISTS idx_atom_video ON knowledge_atom(video_id);
CREATE INDEX IF NOT EXISTS idx_ev_atom ON evidence_ref(atom_id);
CREATE INDEX IF NOT EXISTS idx_job_status ON job(status);

CREATE VIRTUAL TABLE IF NOT EXISTS atom_fts USING fts5(
  atom_id UNINDEXED, claim, reason, category UNINDEXED, space UNINDEXED
);
