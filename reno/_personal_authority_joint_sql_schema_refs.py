"""Fixed immutable first-profile candidates from actual r891 A/r893 B.
No runtime publication, data-file reading or SQLite execution.
"""

PROFILE_KEY = 'fresh17.admission2_dispose2.receiver_absent'

REFERENCE_SQLITE_VERSION = '3.49.1'

BASE_OBJECTS = (('index',
  'idx_atom_video',
  'knowledge_atom',
  'CREATE INDEX idx_atom_video ON knowledge_atom(video_id)'),
 ('index',
  'idx_budget_attempt_operation',
  'budget_attempt',
  'CREATE INDEX idx_budget_attempt_operation ON budget_attempt(operation_id,state)'),
 ('index',
  'idx_budget_attempt_ordinal',
  'budget_attempt',
  'CREATE UNIQUE INDEX idx_budget_attempt_ordinal ON budget_attempt(operation_id,ordinal)'),
 ('index',
  'idx_budget_attempt_window',
  'budget_attempt',
  'CREATE INDEX idx_budget_attempt_window ON '
  'budget_attempt(state,current_window_start_utc,current_window_end_utc)'),
 ('index',
  'idx_budget_dispatch_token',
  'budget_attempt',
  'CREATE UNIQUE INDEX idx_budget_dispatch_token ON budget_attempt(dispatch_token_sha256) WHERE '
  'dispatch_token_sha256 IS NOT NULL'),
 ('index',
  'idx_budget_job_lineage',
  'job',
  'CREATE INDEX idx_budget_job_lineage ON job(continuation_id,execution_generation)'),
 ('index',
  'idx_budget_operation_identity',
  'budget_operation',
  'CREATE UNIQUE INDEX idx_budget_operation_identity ON budget_operation(identity_sha256)'),
 ('index',
  'idx_budget_operation_scope',
  'budget_operation',
  'CREATE UNIQUE INDEX idx_budget_operation_scope ON '
  'budget_operation(library_id,continuation_id,execution_generation,kind,suboperation_key,input_sha256)'),
 ('index',
  'idx_decision_event',
  'user_decision',
  'CREATE UNIQUE INDEX idx_decision_event ON user_decision(event_id)'),
 ('index', 'idx_ev_atom', 'evidence_ref', 'CREATE INDEX idx_ev_atom ON evidence_ref(atom_id)'),
 ('index',
  'idx_gc_item_state',
  'storage_gc_item',
  'CREATE INDEX idx_gc_item_state ON storage_gc_item(operation_id,state)'),
 ('index',
  'idx_home_recovery_committed',
  'home_knowledge_recovery_operation',
  'CREATE INDEX idx_home_recovery_committed ON '
  'home_knowledge_recovery_operation(committed_at,operation_id)'),
 ('index',
  'idx_home_recovery_epoch',
  'home_knowledge_recovery_operation',
  'CREATE UNIQUE INDEX idx_home_recovery_epoch ON '
  'home_knowledge_recovery_operation(library_id,after_library_epoch)'),
 ('index',
  'idx_home_recovery_root',
  'home_knowledge_recovery_operation',
  'CREATE INDEX idx_home_recovery_root ON '
  'home_knowledge_recovery_operation(root_job_id,execution_generation,committed_at,operation_id) '
  'WHERE root_job_id IS NOT NULL'),
 ('index',
  'idx_job_active_scope',
  'job',
  'CREATE UNIQUE INDEX idx_job_active_scope ON job(scope_key)\n'
  "  WHERE scope_key IS NOT NULL AND status IN ('pending','running')"),
 ('index', 'idx_job_request', 'job', 'CREATE UNIQUE INDEX idx_job_request ON job(request_id)'),
 ('index', 'idx_job_status', 'job', 'CREATE INDEX idx_job_status ON job(status)'),
 ('index',
  'idx_ocr_video',
  'ocr_span',
  'CREATE INDEX idx_ocr_video ON ocr_span(video_id, start_ms)'),
 ('index',
  'idx_personal_attachment_source',
  'source_attachment',
  'CREATE INDEX idx_personal_attachment_source ON source_attachment(source_id,state)'),
 ('index',
  'idx_personal_card',
  'decision_card',
  'CREATE INDEX idx_personal_card ON decision_card(project_id,state,updated_at,card_id)'),
 ('index',
  'idx_personal_card_space_stage',
  'decision_card',
  'CREATE INDEX idx_personal_card_space_stage ON '
  'decision_card(project_id,space,stage,updated_at,card_id)'),
 ('index',
  'idx_personal_check',
  'checklist_check',
  'CREATE INDEX idx_personal_check ON checklist_check(item_id,created_at,check_id)'),
 ('index',
  'idx_personal_item',
  'checklist_item',
  'CREATE INDEX idx_personal_item ON checklist_item(instance_id,position,item_id)'),
 ('index',
  'idx_personal_ref_owner',
  'personal_evidence_ref',
  'CREATE INDEX idx_personal_ref_owner ON personal_evidence_ref(owner_kind,owner_id)'),
 ('index',
  'idx_personal_ref_target',
  'personal_evidence_ref',
  'CREATE INDEX idx_personal_ref_target ON personal_evidence_ref(target_kind,target_id)'),
 ('index',
  'idx_personal_source',
  'source_record',
  'CREATE INDEX idx_personal_source ON source_record(project_id,updated_at,source_id)'),
 ('index',
  'idx_seg_video',
  'transcript_segment',
  'CREATE INDEX idx_seg_video ON transcript_segment(video_id, start_ms)'),
 ('index',
  'idx_source_admission_claim',
  'sync_source_admission',
  'CREATE UNIQUE INDEX idx_source_admission_claim ON '
  'sync_source_admission(item_id,lease_generation)'),
 ('index',
  'idx_source_admission_core',
  'sync_source_admission',
  'CREATE INDEX idx_source_admission_core ON '
  'sync_source_admission(core_sha256,item_id,committed_at,admission_id)'),
 ('index',
  'idx_source_admission_first',
  'sync_source_admission',
  'CREATE UNIQUE INDEX idx_source_admission_first ON sync_source_admission(core_sha256,item_id) '
  'WHERE previous_admission_id IS NULL'),
 ('index',
  'idx_source_admission_previous',
  'sync_source_admission',
  'CREATE UNIQUE INDEX idx_source_admission_previous ON '
  'sync_source_admission(previous_admission_id) WHERE previous_admission_id IS NOT NULL'),
 ('index',
  'idx_source_continuity_identity',
  'sync_source_continuity_activation',
  'CREATE UNIQUE INDEX idx_source_continuity_identity ON '
  'sync_source_continuity_activation(owner_profile_sha256,source_library_id,source_node_id,target_library_id,target_node_id,target_epoch)'),
 ('index',
  'idx_source_disposition_core',
  'sync_source_control_disposition',
  'CREATE INDEX idx_source_disposition_core ON '
  'sync_source_control_disposition(core_sha256,item_id,committed_at,request_id)'),
 ('index',
  'idx_source_disposition_original',
  'sync_source_control_disposition',
  'CREATE UNIQUE INDEX idx_source_disposition_original ON '
  'sync_source_control_disposition(original_request_id)'),
 ('index',
  'idx_source_registration_core',
  'sync_source_control_registration',
  'CREATE INDEX idx_source_registration_core ON '
  'sync_source_control_registration(core_sha256,item_id,committed_at,request_id)'),
 ('index',
  'idx_source_registration_original',
  'sync_source_control_registration',
  'CREATE UNIQUE INDEX idx_source_registration_original ON '
  'sync_source_control_registration(original_request_id)'),
 ('index',
  'idx_source_registration_sha',
  'sync_source_control_registration',
  'CREATE UNIQUE INDEX idx_source_registration_sha ON '
  'sync_source_control_registration(registration_sha256)'),
 ('index',
  'idx_sync_budget_claim_defer',
  'sync_queue_budget_receipt',
  'CREATE UNIQUE INDEX idx_sync_budget_claim_defer ON '
  "sync_queue_budget_receipt(item_id,lease_generation) WHERE action='budget_defer'"),
 ('index',
  'idx_sync_checkpoint_created',
  'sync_checkpoint',
  'CREATE INDEX idx_sync_checkpoint_created ON sync_checkpoint(created_at,checkpoint_id)'),
 ('index',
  'idx_sync_publication_source',
  'sync_publication',
  'CREATE INDEX idx_sync_publication_source ON '
  'sync_publication(source_library_id,source_node_id,source_epoch,source_revision)'),
 ('index',
  'idx_sync_publication_video',
  'sync_published_video',
  'CREATE INDEX idx_sync_publication_video ON sync_published_video(video_id,committed_at)'),
 ('index',
  'idx_sync_recovery_checkpoint_created',
  'sync_recovery_checkpoint',
  'CREATE INDEX idx_sync_recovery_checkpoint_created ON '
  'sync_recovery_checkpoint(created_at,checkpoint_id)'),
 ('index',
  'idx_sync_recovery_created',
  'sync_recovery_operation',
  'CREATE INDEX idx_sync_recovery_created ON sync_recovery_operation(created_at,operation_id)'),
 ('index',
  'idx_sync_source_terminal_item',
  'sync_queue_source_receipt',
  'CREATE UNIQUE INDEX idx_sync_source_terminal_item ON sync_queue_source_receipt(item_id)'),
 ('index',
  'idx_syncq_claim',
  'sync_queue',
  'CREATE INDEX idx_syncq_claim ON sync_queue(status,retry_at,item_id)'),
 ('index',
  'idx_syncq_lease',
  'sync_queue',
  'CREATE INDEX idx_syncq_lease ON sync_queue(status,lease_until)'),
 ('index',
  'idx_syncq_owner',
  'sync_queue',
  'CREATE INDEX idx_syncq_owner ON sync_queue(owner_id,owner_generation,status)'),
 ('index', 'idx_syncq_status', 'sync_queue', 'CREATE INDEX idx_syncq_status ON sync_queue(status)'),
 ('index',
  'idx_upload_abandon',
  'sync_upload_pin',
  'CREATE UNIQUE INDEX idx_upload_abandon ON sync_upload_pin(abandon_operation_id) WHERE '
  'abandon_operation_id IS NOT NULL'),
 ('index',
  'idx_upload_descriptor',
  'sync_upload_pin',
  'CREATE UNIQUE INDEX idx_upload_descriptor ON sync_upload_pin(parent_pin_id,descriptor_json) '
  "WHERE kind='media'"),
 ('index',
  'idx_upload_hold',
  'sync_upload_pin',
  'CREATE INDEX idx_upload_hold ON sync_upload_pin(state,artifact_id)'),
 ('index',
  'idx_upload_publication',
  'sync_upload_pin',
  'CREATE UNIQUE INDEX idx_upload_publication ON sync_upload_pin(publication_id) WHERE '
  "kind='publication'"),
 ('index',
  'idx_vis_video',
  'visual_observation',
  'CREATE INDEX idx_vis_video ON visual_observation(video_id, pts_ms)'),
 ('index', 'sqlite_autoindex_budget_attempt_1', 'budget_attempt', None),
 ('index', 'sqlite_autoindex_budget_operation_1', 'budget_operation', None),
 ('index', 'sqlite_autoindex_checklist_check_1', 'checklist_check', None),
 ('index', 'sqlite_autoindex_checklist_check_2', 'checklist_check', None),
 ('index', 'sqlite_autoindex_checklist_check_3', 'checklist_check', None),
 ('index', 'sqlite_autoindex_checklist_instance_1', 'checklist_instance', None),
 ('index', 'sqlite_autoindex_checklist_item_1', 'checklist_item', None),
 ('index', 'sqlite_autoindex_conflict_case_1', 'conflict_case', None),
 ('index', 'sqlite_autoindex_decision_card_1', 'decision_card', None),
 ('index', 'sqlite_autoindex_frame_1', 'frame', None),
 ('index',
  'sqlite_autoindex_home_knowledge_recovery_operation_1',
  'home_knowledge_recovery_operation',
  None),
 ('index', 'sqlite_autoindex_knowledge_atom_1', 'knowledge_atom', None),
 ('index', 'sqlite_autoindex_knowledge_cluster_1', 'knowledge_cluster', None),
 ('index', 'sqlite_autoindex_meta_1', 'meta', None),
 ('index', 'sqlite_autoindex_mutation_receipt_1', 'mutation_receipt', None),
 ('index', 'sqlite_autoindex_ocr_span_1', 'ocr_span', None),
 ('index', 'sqlite_autoindex_personal_evidence_ref_1', 'personal_evidence_ref', None),
 ('index', 'sqlite_autoindex_personal_revision_1', 'personal_revision', None),
 ('index', 'sqlite_autoindex_renovation_project_1', 'renovation_project', None),
 ('index', 'sqlite_autoindex_source_attachment_1', 'source_attachment', None),
 ('index', 'sqlite_autoindex_source_attachment_2', 'source_attachment', None),
 ('index', 'sqlite_autoindex_source_attachment_3', 'source_attachment', None),
 ('index', 'sqlite_autoindex_source_record_1', 'source_record', None),
 ('index', 'sqlite_autoindex_storage_artifact_1', 'storage_artifact', None),
 ('index', 'sqlite_autoindex_storage_artifact_2', 'storage_artifact', None),
 ('index', 'sqlite_autoindex_storage_gc_item_1', 'storage_gc_item', None),
 ('index', 'sqlite_autoindex_storage_gc_operation_1', 'storage_gc_operation', None),
 ('index', 'sqlite_autoindex_sync_checkpoint_1', 'sync_checkpoint', None),
 ('index', 'sqlite_autoindex_sync_checkpoint_2', 'sync_checkpoint', None),
 ('index', 'sqlite_autoindex_sync_checkpoint_3', 'sync_checkpoint', None),
 ('index', 'sqlite_autoindex_sync_checkpoint_retirement_1', 'sync_checkpoint_retirement', None),
 ('index', 'sqlite_autoindex_sync_publication_1', 'sync_publication', None),
 ('index', 'sqlite_autoindex_sync_publication_fence_1', 'sync_publication_fence', None),
 ('index', 'sqlite_autoindex_sync_published_video_1', 'sync_published_video', None),
 ('index', 'sqlite_autoindex_sync_queue_budget_receipt_1', 'sync_queue_budget_receipt', None),
 ('index', 'sqlite_autoindex_sync_queue_publication_1', 'sync_queue_publication', None),
 ('index', 'sqlite_autoindex_sync_queue_source_receipt_1', 'sync_queue_source_receipt', None),
 ('index', 'sqlite_autoindex_sync_recovery_checkpoint_1', 'sync_recovery_checkpoint', None),
 ('index', 'sqlite_autoindex_sync_recovery_checkpoint_2', 'sync_recovery_checkpoint', None),
 ('index', 'sqlite_autoindex_sync_recovery_checkpoint_3', 'sync_recovery_checkpoint', None),
 ('index', 'sqlite_autoindex_sync_recovery_operation_1', 'sync_recovery_operation', None),
 ('index', 'sqlite_autoindex_sync_report_1', 'sync_report', None),
 ('index', 'sqlite_autoindex_sync_source_admission_1', 'sync_source_admission', None),
 ('index',
  'sqlite_autoindex_sync_source_continuity_activation_1',
  'sync_source_continuity_activation',
  None),
 ('index',
  'sqlite_autoindex_sync_source_control_disposition_1',
  'sync_source_control_disposition',
  None),
 ('index',
  'sqlite_autoindex_sync_source_control_registration_1',
  'sync_source_control_registration',
  None),
 ('index', 'sqlite_autoindex_sync_upload_pin_1', 'sync_upload_pin', None),
 ('index', 'sqlite_autoindex_task_request_1', 'task_request', None),
 ('index', 'sqlite_autoindex_transcript_segment_1', 'transcript_segment', None),
 ('index', 'sqlite_autoindex_video_asset_1', 'video_asset', None),
 ('index', 'sqlite_autoindex_video_asset_2', 'video_asset', None),
 ('index', 'sqlite_autoindex_visual_observation_1', 'visual_observation', None),
 ('index', 'sqlite_autoindex_worker_lease_1', 'worker_lease', None),
 ('table',
  'atom_fts',
  'atom_fts',
  'CREATE VIRTUAL TABLE atom_fts USING fts5(\n'
  '  atom_id UNINDEXED, claim, reason, category UNINDEXED, space UNINDEXED\n'
  ')'),
 ('table',
  'atom_fts_config',
  'atom_fts_config',
  "CREATE TABLE 'atom_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID"),
 ('table',
  'atom_fts_content',
  'atom_fts_content',
  "CREATE TABLE 'atom_fts_content'(id INTEGER PRIMARY KEY, c0, c1, c2, c3, c4)"),
 ('table',
  'atom_fts_data',
  'atom_fts_data',
  "CREATE TABLE 'atom_fts_data'(id INTEGER PRIMARY KEY, block BLOB)"),
 ('table',
  'atom_fts_docsize',
  'atom_fts_docsize',
  "CREATE TABLE 'atom_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB)"),
 ('table',
  'atom_fts_idx',
  'atom_fts_idx',
  "CREATE TABLE 'atom_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID"),
 ('table',
  'budget_attempt',
  'budget_attempt',
  "CREATE TABLE budget_attempt (attempt_id TEXT NOT NULL CHECK(typeof(attempt_id)='text' AND "
  "length(attempt_id)=36 AND substr(attempt_id,9,1)='-' AND substr(attempt_id,14,1)='-' AND "
  "substr(attempt_id,19,1)='-' AND substr(attempt_id,24,1)='-' AND "
  "length(replace(attempt_id,'-',''))=32 AND replace(attempt_id,'-','') NOT GLOB '*[^0-9a-f]*') "
  'PRIMARY KEY,\n'
  "operation_id TEXT NOT NULL CHECK(typeof(operation_id)='text' AND length(operation_id)=36 AND "
  "substr(operation_id,9,1)='-' AND substr(operation_id,14,1)='-' AND "
  "substr(operation_id,19,1)='-' AND substr(operation_id,24,1)='-' AND "
  "length(replace(operation_id,'-',''))=32 AND replace(operation_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*') REFERENCES budget_operation(operation_id),\n"
  "ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>=1 AND ordinal<=128),\n"
  "request_sha256 TEXT NOT NULL CHECK(typeof(request_sha256)='text' AND length(request_sha256)=64 "
  "AND request_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  'download_segment TEXT CHECK(download_segment IS NULL OR (download_segment IN '
  "('audio','video'))),\n"
  'state TEXT NOT NULL CHECK(state IN '
  "('dispatching','indeterminate','released','reserved','settled')),\n"
  "initial_reservation_json TEXT NOT NULL CHECK(typeof(initial_reservation_json)='text' AND "
  'length(CAST(initial_reservation_json AS BLOB)) BETWEEN 0 AND 65536),\n'
  "initial_reservation_sha256 TEXT NOT NULL CHECK(typeof(initial_reservation_sha256)='text' AND "
  "length(initial_reservation_sha256)=64 AND initial_reservation_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "current_reservation_json TEXT NOT NULL CHECK(typeof(current_reservation_json)='text' AND "
  'length(CAST(current_reservation_json AS BLOB)) BETWEEN 0 AND 65536),\n'
  "current_reservation_sha256 TEXT NOT NULL CHECK(typeof(current_reservation_sha256)='text' AND "
  "length(current_reservation_sha256)=64 AND current_reservation_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "reservation_revision INTEGER NOT NULL DEFAULT 0 CHECK(typeof(reservation_revision)='integer' "
  'AND reservation_revision>=0 AND reservation_revision<=9223372036854775807),\n'
  "reservation_held INTEGER NOT NULL DEFAULT 1 CHECK(typeof(reservation_held)='integer' AND "
  'reservation_held>=0 AND reservation_held<=1),\n'
  "current_policy_sha256 TEXT NOT NULL CHECK(typeof(current_policy_sha256)='text' AND "
  "length(current_policy_sha256)=64 AND current_policy_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "current_period_key TEXT NOT NULL CHECK(typeof(current_period_key)='text' AND "
  'length(CAST(current_period_key AS BLOB)) BETWEEN 1 AND 256),\n'
  "current_window_start_utc TEXT NOT NULL CHECK(typeof(current_window_start_utc)='text' AND "
  'length(current_window_start_utc)=27 AND current_window_start_utc GLOB '
  "'????-??-??T??:??:??.??????Z'),\n"
  "current_window_end_utc TEXT NOT NULL CHECK(typeof(current_window_end_utc)='text' AND "
  'length(current_window_end_utc)=27 AND current_window_end_utc GLOB '
  "'????-??-??T??:??:??.??????Z'),\n"
  'first_dispatch_json TEXT CHECK(first_dispatch_json IS NULL OR '
  "(typeof(first_dispatch_json)='text' AND length(CAST(first_dispatch_json AS BLOB)) BETWEEN 0 AND "
  '65536)),\n'
  'first_dispatch_sha256 TEXT CHECK(first_dispatch_sha256 IS NULL OR '
  "(typeof(first_dispatch_sha256)='text' AND length(first_dispatch_sha256)=64 AND "
  "first_dispatch_sha256 NOT GLOB '*[^0-9a-f]*')),\n"
  'dispatch_token_sha256 TEXT CHECK(dispatch_token_sha256 IS NULL OR '
  "(typeof(dispatch_token_sha256)='text' AND length(dispatch_token_sha256)=64 AND "
  "dispatch_token_sha256 NOT GLOB '*[^0-9a-f]*')),\n"
  "dispatched_at TEXT CHECK(dispatched_at IS NULL OR (typeof(dispatched_at)='text' AND "
  "length(dispatched_at)=27 AND dispatched_at GLOB '????-??-??T??:??:??.??????Z')),\n"
  "last_admitted_at TEXT NOT NULL CHECK(typeof(last_admitted_at)='text' AND "
  "length(last_admitted_at)=27 AND last_admitted_at GLOB '????-??-??T??:??:??.??????Z'),\n"
  "response_complete INTEGER NOT NULL DEFAULT 0 CHECK(typeof(response_complete)='integer' AND "
  'response_complete>=0 AND response_complete<=1),\n'
  'outcome TEXT CHECK(outcome IS NULL OR (outcome IN '
  "('full_result','http_retry','repair','resolution_retry','source_retry','source_step_complete','terminal_error'))),\n"
  'observed_usage_json TEXT CHECK(observed_usage_json IS NULL OR '
  "(typeof(observed_usage_json)='text' AND length(CAST(observed_usage_json AS BLOB)) BETWEEN 0 AND "
  '65536)),\n'
  "usage_quality TEXT NOT NULL DEFAULT 'missing' CHECK(usage_quality IN "
  "('invalid','missing','partial','reported')),\n"
  'provider_request_id TEXT CHECK(provider_request_id IS NULL OR '
  "(typeof(provider_request_id)='text' AND length(CAST(provider_request_id AS BLOB)) BETWEEN 0 AND "
  '256)),\n'
  "result_text TEXT CHECK(result_text IS NULL OR (typeof(result_text)='text' AND "
  'length(CAST(result_text AS BLOB)) BETWEEN 0 AND 2097152)),\n'
  "result_sha256 TEXT CHECK(result_sha256 IS NULL OR (typeof(result_sha256)='text' AND "
  "length(result_sha256)=64 AND result_sha256 NOT GLOB '*[^0-9a-f]*')),\n"
  "result_bytes INTEGER NOT NULL DEFAULT 0 CHECK(typeof(result_bytes)='integer' AND "
  'result_bytes>=0 AND result_bytes<=9223372036854775807),\n'
  "result_storage_state TEXT NOT NULL DEFAULT 'none' CHECK(result_storage_state IN "
  "('capacity_blocked','none','oversize','retired','stored')),\n"
  "settlement_sha256 TEXT CHECK(settlement_sha256 IS NULL OR (typeof(settlement_sha256)='text' AND "
  "length(settlement_sha256)=64 AND settlement_sha256 NOT GLOB '*[^0-9a-f]*')),\n"
  "settled_at TEXT CHECK(settled_at IS NULL OR (typeof(settled_at)='text' AND "
  "length(settled_at)=27 AND settled_at GLOB '????-??-??T??:??:??.??????Z')),\n"
  "indeterminate_at TEXT CHECK(indeterminate_at IS NULL OR (typeof(indeterminate_at)='text' AND "
  "length(indeterminate_at)=27 AND indeterminate_at GLOB '????-??-??T??:??:??.??????Z')),\n"
  "created_at TEXT NOT NULL CHECK(typeof(created_at)='text' AND length(created_at)=27 AND "
  "created_at GLOB '????-??-??T??:??:??.??????Z'),\n"
  "updated_at TEXT NOT NULL CHECK(typeof(updated_at)='text' AND length(updated_at)=27 AND "
  "updated_at GLOB '????-??-??T??:??:??.??????Z'),\n"
  'CHECK(current_window_start_utc<current_window_end_utc),\n'
  "CHECK((state IN ('reserved','released') AND first_dispatch_json IS NULL AND "
  'first_dispatch_sha256 IS NULL AND dispatch_token_sha256 IS NULL AND dispatched_at IS NULL) OR '
  "(state IN ('dispatching','indeterminate','settled') AND first_dispatch_json IS NOT NULL AND "
  'first_dispatch_sha256 IS NOT NULL AND dispatch_token_sha256 IS NOT NULL AND dispatched_at IS '
  'NOT NULL)),\n'
  "CHECK((state='settled' AND response_complete=1 AND outcome IS NOT NULL AND settlement_sha256 IS "
  "NOT NULL AND settled_at IS NOT NULL) OR (state!='settled' AND response_complete=0 AND outcome "
  'IS NULL AND settlement_sha256 IS NULL AND settled_at IS NULL)),\n'
  "CHECK((result_storage_state='stored' AND result_text IS NOT NULL AND result_sha256 IS NOT NULL "
  "AND result_bytes=length(CAST(result_text AS BLOB))) OR (result_storage_state!='stored' AND "
  'result_text IS NULL)))'),
 ('table',
  'budget_operation',
  'budget_operation',
  "CREATE TABLE budget_operation (operation_id TEXT NOT NULL CHECK(typeof(operation_id)='text' AND "
  "length(operation_id)=36 AND substr(operation_id,9,1)='-' AND substr(operation_id,14,1)='-' AND "
  "substr(operation_id,19,1)='-' AND substr(operation_id,24,1)='-' AND "
  "length(replace(operation_id,'-',''))=32 AND replace(operation_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*') PRIMARY KEY,\n"
  "library_id TEXT NOT NULL CHECK(typeof(library_id)='text' AND length(library_id)=36 AND "
  "substr(library_id,9,1)='-' AND substr(library_id,14,1)='-' AND substr(library_id,19,1)='-' AND "
  "substr(library_id,24,1)='-' AND length(replace(library_id,'-',''))=32 AND "
  "replace(library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "identity_sha256 TEXT NOT NULL CHECK(typeof(identity_sha256)='text' AND "
  "length(identity_sha256)=64 AND identity_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "continuation_id TEXT NOT NULL CHECK(typeof(continuation_id)='text' AND "
  "length(continuation_id)=36 AND substr(continuation_id,9,1)='-' AND "
  "substr(continuation_id,14,1)='-' AND substr(continuation_id,19,1)='-' AND "
  "substr(continuation_id,24,1)='-' AND length(replace(continuation_id,'-',''))=32 AND "
  "replace(continuation_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "execution_generation INTEGER NOT NULL CHECK(typeof(execution_generation)='integer' AND "
  'execution_generation>=1 AND execution_generation<=9223372036854775807),\n'
  'kind TEXT NOT NULL CHECK(kind IN '
  "('download','model','source_enumeration','source_metadata','source_resolution')),\n"
  "suboperation_key TEXT NOT NULL CHECK(typeof(suboperation_key)='text' AND "
  'length(CAST(suboperation_key AS BLOB)) BETWEEN 1 AND 512),\n'
  "input_sha256 TEXT NOT NULL CHECK(typeof(input_sha256)='text' AND length(input_sha256)=64 AND "
  "input_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "root_job_id INTEGER CHECK(root_job_id IS NULL OR (typeof(root_job_id)='integer' AND "
  'root_job_id>=1 AND root_job_id<=9223372036854775807)) REFERENCES job(job_id),\n'
  "max_attempts INTEGER NOT NULL DEFAULT 8 CHECK(typeof(max_attempts)='integer' AND "
  'max_attempts>=1 AND max_attempts<=128),\n'
  "state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('completed','exhausted','open','waiting')),\n"
  'wait_reason TEXT CHECK(wait_reason IS NULL OR (wait_reason IN '
  "('attempt_limit','clock_rollback','daily_limit','download_pacing','indeterminate','ledger_unavailable','manual_pause','output_limit','reported_tokens_limit','result_capacity','unsupported_capability'))),\n"
  "resume_at TEXT CHECK(resume_at IS NULL OR (typeof(resume_at)='text' AND length(resume_at)=27 "
  "AND resume_at GLOB '????-??-??T??:??:??.??????Z')),\n"
  "wait_details_json TEXT NOT NULL DEFAULT '{}' CHECK(typeof(wait_details_json)='text' AND "
  'length(CAST(wait_details_json AS BLOB)) BETWEEN 0 AND 65536),\n'
  "revision INTEGER NOT NULL DEFAULT 0 CHECK(typeof(revision)='integer' AND revision>=0 AND "
  'revision<=9223372036854775807),\n'
  "created_at TEXT NOT NULL CHECK(typeof(created_at)='text' AND length(created_at)=27 AND "
  "created_at GLOB '????-??-??T??:??:??.??????Z'),\n"
  "updated_at TEXT NOT NULL CHECK(typeof(updated_at)='text' AND length(updated_at)=27 AND "
  "updated_at GLOB '????-??-??T??:??:??.??????Z'),\n"
  'CHECK(resume_at IS NULL OR wait_reason IS NOT NULL))'),
 ('table',
  'checklist_check',
  'checklist_check',
  'CREATE TABLE checklist_check (check_id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES '
  'checklist_item(item_id),\n'
  '        item_revision INTEGER NOT NULL, criteria_snapshot_hash TEXT NOT NULL,\n'
  '        operation_id TEXT NOT NULL UNIQUE, commit_seq INTEGER NOT NULL,\n'
  "        record_kind TEXT NOT NULL CHECK(record_kind IN ('inspection','remediation','void')), "
  'result TEXT,\n'
  "        measurements_json TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',\n"
  "        attachment_refs_json TEXT NOT NULL DEFAULT '[]', checked_at TEXT NOT NULL, created_at "
  'TEXT NOT NULL,\n'
  '        supersedes_check_id TEXT REFERENCES checklist_check(check_id), '
  'UNIQUE(item_id,commit_seq))'),
 ('table',
  'checklist_instance',
  'checklist_instance',
  'CREATE TABLE checklist_instance (instance_id TEXT PRIMARY KEY, project_id TEXT NOT NULL '
  'REFERENCES renovation_project(project_id),\n'
  "        title TEXT NOT NULL, space TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '', "
  'basis_kb_revision INTEGER,\n'
  "        lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK(lifecycle_state IN "
  "('active','archived')),\n"
  '        revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT '
  'NULL, archived_at TEXT)'),
 ('table',
  'checklist_item',
  'checklist_item',
  'CREATE TABLE checklist_item (item_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES '
  'checklist_instance(instance_id),\n'
  "        title TEXT NOT NULL, conditions_json TEXT NOT NULL DEFAULT '{}', method_text TEXT NOT "
  "NULL DEFAULT '',\n"
  "        basis_kind TEXT NOT NULL, criteria_json TEXT NOT NULL DEFAULT '{}',\n"
  "        criteria_state TEXT NOT NULL DEFAULT 'needs_verification' CHECK(criteria_state IN "
  "('known','needs_verification')),\n"
  '        position INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,\n'
  '        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'),
 ('table',
  'conflict_case',
  'conflict_case',
  'CREATE TABLE conflict_case (\n'
  '  conflict_id TEXT PRIMARY KEY,\n'
  '  ctype TEXT,\n'
  '  status TEXT,\n'
  '  side_a_json TEXT,\n'
  '  side_b_json TEXT,\n'
  '  analysis_json TEXT,\n'
  '  recommended_action TEXT,\n'
  '  created_at TEXT,\n'
  '  derivation_json TEXT\n'
  ')'),
 ('table',
  'decision_card',
  'decision_card',
  'CREATE TABLE decision_card (card_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES '
  'renovation_project(project_id),\n'
  "        title TEXT NOT NULL, question TEXT NOT NULL DEFAULT '', context_json TEXT NOT NULL "
  "DEFAULT '{}',\n"
  "        space TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '',\n"
  "        alternatives_json TEXT NOT NULL DEFAULT '[]', selected_option_id TEXT, decision_text "
  "TEXT NOT NULL DEFAULT '',\n"
  "        decision_reason TEXT NOT NULL DEFAULT '', open_questions_json TEXT NOT NULL DEFAULT "
  "'[]',\n"
  "        content_snapshot_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT "
  "'considering'\n"
  "        CHECK(state IN ('considering','adopted','not_adopted','archived')), revision INTEGER "
  'NOT NULL DEFAULT 1,\n'
  '        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT)'),
 ('table',
  'evidence_ref',
  'evidence_ref',
  'CREATE TABLE evidence_ref (\n'
  '  id INTEGER PRIMARY KEY AUTOINCREMENT,\n'
  '  atom_id TEXT,\n'
  '  video_id TEXT,\n'
  '  modality TEXT,\n'
  '  source_item_id TEXT,\n'
  '  start_ms INTEGER,\n'
  '  end_ms INTEGER,\n'
  '  evidence_text TEXT,\n'
  '  weight REAL\n'
  ')'),
 ('table',
  'frame',
  'frame',
  'CREATE TABLE frame (\n'
  '  frame_id TEXT NOT NULL,\n'
  '  video_id TEXT,\n'
  '  pts_ms INTEGER,\n'
  '  origin TEXT,\n'
  '  dhash TEXT,\n'
  '  path TEXT,\n'
  '  PRIMARY KEY (video_id, frame_id)\n'
  ')'),
 ('table',
  'home_knowledge_recovery_operation',
  'home_knowledge_recovery_operation',
  'CREATE TABLE home_knowledge_recovery_operation (operation_id TEXT NOT NULL '
  "CHECK(typeof(operation_id)='text' AND length(operation_id)=36 AND substr(operation_id,9,1)='-' "
  "AND substr(operation_id,14,1)='-' AND substr(operation_id,19,1)='-' AND "
  "substr(operation_id,24,1)='-' AND length(replace(operation_id,'-',''))=32 AND "
  "replace(operation_id,'-','') NOT GLOB '*[^0-9a-f]*') PRIMARY KEY,\n"
  "format_version INTEGER NOT NULL CHECK(typeof(format_version)='integer' AND format_version=1),\n"
  "action TEXT NOT NULL CHECK(action='knowledge_restore'),\n"
  "library_id TEXT NOT NULL CHECK(typeof(library_id)='text' AND length(library_id)=36 AND "
  "substr(library_id,9,1)='-' AND substr(library_id,14,1)='-' AND substr(library_id,19,1)='-' AND "
  "substr(library_id,24,1)='-' AND length(replace(library_id,'-',''))=32 AND "
  "replace(library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "node_id TEXT NOT NULL CHECK(typeof(node_id)='text' AND length(node_id)=36 AND "
  "substr(node_id,9,1)='-' AND substr(node_id,14,1)='-' AND substr(node_id,19,1)='-' AND "
  "substr(node_id,24,1)='-' AND length(replace(node_id,'-',''))=32 AND replace(node_id,'-','') NOT "
  "GLOB '*[^0-9a-f]*'),\n"
  "source_kind TEXT NOT NULL CHECK(source_kind IN ('registered_backup','home_checkpoint')),\n"
  "source_id TEXT NOT NULL CHECK(typeof(source_id)='text' AND length(source_id)=36 AND "
  "substr(source_id,9,1)='-' AND substr(source_id,14,1)='-' AND substr(source_id,19,1)='-' AND "
  "substr(source_id,24,1)='-' AND length(replace(source_id,'-',''))=32 AND "
  "replace(source_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "source_sha256 TEXT NOT NULL CHECK(typeof(source_sha256)='text' AND length(source_sha256)=64 AND "
  "source_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "source_binding_sha256 TEXT NOT NULL CHECK(typeof(source_binding_sha256)='text' AND "
  "length(source_binding_sha256)=64 AND source_binding_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "before_library_epoch INTEGER NOT NULL CHECK(typeof(before_library_epoch)='integer' AND "
  'before_library_epoch BETWEEN 1 AND 9223372036854775806),\n'
  "before_kb_revision INTEGER NOT NULL CHECK(typeof(before_kb_revision)='integer' AND "
  'before_kb_revision BETWEEN 0 AND 9223372036854775806),\n'
  "after_library_epoch INTEGER NOT NULL CHECK(typeof(after_library_epoch)='integer' AND "
  'after_library_epoch BETWEEN 2 AND 9223372036854775807),\n'
  "after_kb_revision INTEGER NOT NULL CHECK(typeof(after_kb_revision)='integer' AND "
  'after_kb_revision BETWEEN 1 AND 9223372036854775807),\n'
  "before_version_json TEXT NOT NULL CHECK(typeof(before_version_json)='text' AND "
  'length(CAST(before_version_json AS BLOB)) BETWEEN 2 AND 512) '
  'CHECK(json_valid(before_version_json)) CHECK(CASE WHEN json_valid(before_version_json) THEN '
  "json_type(before_version_json)='object' ELSE 0 END),\n"
  "before_version_sha256 TEXT NOT NULL CHECK(typeof(before_version_sha256)='text' AND "
  "length(before_version_sha256)=64 AND before_version_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "after_version_json TEXT NOT NULL CHECK(typeof(after_version_json)='text' AND "
  'length(CAST(after_version_json AS BLOB)) BETWEEN 2 AND 512) '
  'CHECK(json_valid(after_version_json)) CHECK(CASE WHEN json_valid(after_version_json) THEN '
  "json_type(after_version_json)='object' ELSE 0 END),\n"
  "after_version_sha256 TEXT NOT NULL CHECK(typeof(after_version_sha256)='text' AND "
  "length(after_version_sha256)=64 AND after_version_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "protection_backup_id TEXT NOT NULL CHECK(typeof(protection_backup_id)='text' AND "
  "length(protection_backup_id)=36 AND substr(protection_backup_id,9,1)='-' AND "
  "substr(protection_backup_id,14,1)='-' AND substr(protection_backup_id,19,1)='-' AND "
  "substr(protection_backup_id,24,1)='-' AND length(replace(protection_backup_id,'-',''))=32 AND "
  "replace(protection_backup_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "protection_descriptor_sha256 TEXT NOT NULL CHECK(typeof(protection_descriptor_sha256)='text' "
  'AND length(protection_descriptor_sha256)=64 AND protection_descriptor_sha256 NOT GLOB '
  "'*[^0-9a-f]*'),\n"
  "protection_image_sha256 TEXT NOT NULL CHECK(typeof(protection_image_sha256)='text' AND "
  "length(protection_image_sha256)=64 AND protection_image_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  'original_core_sha256 TEXT CHECK(original_core_sha256 IS NULL OR '
  "(typeof(original_core_sha256)='text' AND length(original_core_sha256)=64 AND "
  "original_core_sha256 NOT GLOB '*[^0-9a-f]*')),\n"
  "root_job_id INTEGER CHECK(root_job_id IS NULL OR (typeof(root_job_id)='integer' AND root_job_id "
  'BETWEEN 1 AND 9223372036854775807)) REFERENCES job(job_id),\n'
  "continuation_id TEXT CHECK(continuation_id IS NULL OR (typeof(continuation_id)='text' AND "
  "length(continuation_id)=36 AND substr(continuation_id,9,1)='-' AND "
  "substr(continuation_id,14,1)='-' AND substr(continuation_id,19,1)='-' AND "
  "substr(continuation_id,24,1)='-' AND length(replace(continuation_id,'-',''))=32 AND "
  "replace(continuation_id,'-','') NOT GLOB '*[^0-9a-f]*')),\n"
  'execution_generation INTEGER CHECK(execution_generation IS NULL OR '
  "(typeof(execution_generation)='integer' AND execution_generation BETWEEN 1 AND "
  '9223372036854775807)),\n'
  "frontier_json TEXT NOT NULL CHECK(typeof(frontier_json)='text' AND length(CAST(frontier_json AS "
  'BLOB)) BETWEEN 2 AND 8388608) CHECK(json_valid(frontier_json)) CHECK(CASE WHEN '
  "json_valid(frontier_json) THEN json_type(frontier_json)='object' ELSE 0 END),\n"
  "frontier_sha256 TEXT NOT NULL CHECK(typeof(frontier_sha256)='text' AND "
  "length(frontier_sha256)=64 AND frontier_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "before_operational_sha256 TEXT NOT NULL CHECK(typeof(before_operational_sha256)='text' AND "
  "length(before_operational_sha256)=64 AND before_operational_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "after_operational_sha256 TEXT NOT NULL CHECK(typeof(after_operational_sha256)='text' AND "
  "length(after_operational_sha256)=64 AND after_operational_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "checkpoint_json TEXT NOT NULL CHECK(typeof(checkpoint_json)='text' AND "
  'length(CAST(checkpoint_json AS BLOB)) BETWEEN 2 AND 16384) CHECK(json_valid(checkpoint_json)) '
  "CHECK(CASE WHEN json_valid(checkpoint_json) THEN json_type(checkpoint_json)='object' ELSE 0 "
  'END),\n'
  "checkpoint_sha256 TEXT NOT NULL CHECK(typeof(checkpoint_sha256)='text' AND "
  "length(checkpoint_sha256)=64 AND checkpoint_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "request_json TEXT NOT NULL CHECK(typeof(request_json)='text' AND length(CAST(request_json AS "
  'BLOB)) BETWEEN 2 AND 16384) CHECK(json_valid(request_json)) CHECK(CASE WHEN '
  "json_valid(request_json) THEN json_type(request_json)='object' ELSE 0 END),\n"
  "request_sha256 TEXT NOT NULL CHECK(typeof(request_sha256)='text' AND length(request_sha256)=64 "
  "AND request_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "result_json TEXT NOT NULL CHECK(typeof(result_json)='text' AND length(CAST(result_json AS "
  'BLOB)) BETWEEN 2 AND 16384) CHECK(json_valid(result_json)) CHECK(CASE WHEN '
  "json_valid(result_json) THEN json_type(result_json)='object' ELSE 0 END),\n"
  "result_sha256 TEXT NOT NULL CHECK(typeof(result_sha256)='text' AND length(result_sha256)=64 AND "
  "result_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "committed_at TEXT NOT NULL CHECK(typeof(committed_at)='text' AND length(committed_at)=27 AND "
  "committed_at GLOB '????-??-??T??:??:??.??????Z'),\n"
  'CHECK(after_library_epoch=before_library_epoch+1),\n'
  'CHECK(after_kb_revision>before_kb_revision),\n'
  'CHECK(before_operational_sha256=after_operational_sha256),\n'
  'CHECK((original_core_sha256 IS NULL AND root_job_id IS NULL AND continuation_id IS NULL AND '
  'execution_generation IS NULL) OR (original_core_sha256 IS NOT NULL AND root_job_id IS NOT NULL '
  'AND continuation_id IS NOT NULL AND execution_generation IS NOT NULL)))'),
 ('table',
  'job',
  'job',
  'CREATE TABLE job (\n'
  '  job_id INTEGER PRIMARY KEY AUTOINCREMENT,\n'
  '  video_id TEXT,\n'
  '  kind TEXT,\n'
  "  status TEXT DEFAULT 'pending',\n"
  '  attempts INTEGER DEFAULT 0,\n'
  '  error TEXT,\n'
  '  created_at TEXT,\n'
  '  updated_at TEXT,\n'
  '  request_id TEXT,\n'
  '  scope_key TEXT,\n'
  "  payload_json TEXT NOT NULL DEFAULT '{}',\n"
  '  owner_id TEXT,\n'
  '  lease_until INTEGER,\n'
  '  result_json TEXT,\n'
  '  started_at TEXT,\n'
  '  completed_at TEXT\n'
  ", continuation_id TEXT NULL CHECK(continuation_id IS NULL OR (typeof(continuation_id)='text' "
  "AND length(continuation_id)=36 AND substr(continuation_id,9,1)='-' AND "
  "substr(continuation_id,14,1)='-' AND substr(continuation_id,19,1)='-' AND "
  "substr(continuation_id,24,1)='-' AND length(replace(continuation_id,'-',''))=32 AND "
  "replace(continuation_id,'-','') NOT GLOB '*[^0-9a-f]*')), execution_generation INTEGER NOT NULL "
  "DEFAULT 1 CHECK(typeof(execution_generation)='integer' AND execution_generation>=1 AND "
  'execution_generation<=9223372036854775807), not_before INTEGER CHECK(not_before IS NULL OR '
  "(typeof(not_before)='integer' AND not_before>=0 AND not_before<=9223372036854775807)), "
  'blocked_reason TEXT CHECK(blocked_reason IS NULL OR (blocked_reason IN '
  "('attempt_limit','clock_rollback','daily_limit','download_pacing','indeterminate','ledger_unavailable','manual_pause','output_limit','reported_tokens_limit','result_capacity','unsupported_capability'))), "
  "wait_mode TEXT NOT NULL DEFAULT 'none' CHECK(wait_mode IN ('none','until','blocked')) "
  "CHECK((wait_mode='none' AND not_before IS NULL AND blocked_reason IS NULL) OR "
  "(wait_mode='until' AND not_before IS NOT NULL AND blocked_reason IS NOT NULL) OR "
  "(wait_mode='blocked' AND not_before IS NULL AND blocked_reason IS NOT NULL)), manual_paused "
  "INTEGER NOT NULL DEFAULT 0 CHECK(typeof(manual_paused)='integer' AND manual_paused>=0 AND "
  'manual_paused<=1), budget_operation_id TEXT NULL CHECK(budget_operation_id IS NULL OR '
  "(typeof(budget_operation_id)='text' AND length(budget_operation_id)=36 AND "
  "substr(budget_operation_id,9,1)='-' AND substr(budget_operation_id,14,1)='-' AND "
  "substr(budget_operation_id,19,1)='-' AND substr(budget_operation_id,24,1)='-' AND "
  "length(replace(budget_operation_id,'-',''))=32 AND replace(budget_operation_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*')) REFERENCES budget_operation(operation_id))"),
 ('table',
  'knowledge_atom',
  'knowledge_atom',
  'CREATE TABLE knowledge_atom (\n'
  '  id TEXT PRIMARY KEY,\n'
  '  video_id TEXT,\n'
  '  category TEXT,\n'
  '  stage TEXT,\n'
  '  space TEXT,\n'
  '  subject TEXT,\n'
  '  claim TEXT,\n'
  '  reason TEXT,\n'
  '  risk_if_ignored TEXT,\n'
  '  polarity TEXT,\n'
  '  conditions_json TEXT,\n'
  '  parameters_json TEXT,\n'
  '  confidence REAL,\n'
  "  status TEXT DEFAULT 'candidate',\n"
  '  cluster_id TEXT,\n'
  '  model TEXT,\n'
  '  prompt_version TEXT,\n'
  '  created_at TEXT,\n'
  '  dimension TEXT,\n'
  '  evidence_nature TEXT,\n'
  '  exceptions TEXT,\n'
  '  prices_json TEXT\n'
  ')'),
 ('table',
  'knowledge_cluster',
  'knowledge_cluster',
  'CREATE TABLE knowledge_cluster (\n'
  '  cluster_id TEXT PRIMARY KEY,\n'
  '  canonical_topic TEXT,\n'
  '  relation TEXT,\n'
  '  judge_reason TEXT,\n'
  '  members_json TEXT,\n'
  '  linked_conflict TEXT,\n'
  '  created_at TEXT,\n'
  '  derivation_json TEXT\n'
  ')'),
 ('table', 'meta', 'meta', 'CREATE TABLE meta (\n  key TEXT PRIMARY KEY,\n  value TEXT\n)'),
 ('table',
  'mutation_receipt',
  'mutation_receipt',
  'CREATE TABLE mutation_receipt (operation_id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_hash '
  'TEXT NOT NULL,\n'
  '        response_status INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT '
  'NULL)'),
 ('table',
  'ocr_span',
  'ocr_span',
  'CREATE TABLE ocr_span (\n'
  '  id TEXT NOT NULL,\n'
  '  video_id TEXT,\n'
  '  start_ms INTEGER,\n'
  '  end_ms INTEGER,\n'
  '  text TEXT,\n'
  '  conf REAL,\n'
  '  frame_ids_json TEXT,\n'
  '  PRIMARY KEY (video_id, id)\n'
  ')'),
 ('table',
  'personal_evidence_ref',
  'personal_evidence_ref',
  'CREATE TABLE personal_evidence_ref (ref_id TEXT PRIMARY KEY, project_id TEXT NOT NULL '
  'REFERENCES renovation_project(project_id),\n'
  '        owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, target_kind TEXT NOT NULL, target_id '
  'TEXT NOT NULL,\n'
  '        fingerprint_version INTEGER NOT NULL DEFAULT 1, target_fingerprint TEXT, snapshot_json '
  'TEXT NOT NULL,\n'
  '        created_at TEXT NOT NULL)'),
 ('table',
  'personal_revision',
  'personal_revision',
  'CREATE TABLE personal_revision (entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, revision '
  'INTEGER NOT NULL,\n'
  '        snapshot_json TEXT NOT NULL, operation_id TEXT, created_at TEXT NOT NULL,\n'
  '        PRIMARY KEY(entity_kind,entity_id,revision))'),
 ('table',
  'processing_run',
  'processing_run',
  'CREATE TABLE processing_run (\n'
  '  run_id INTEGER PRIMARY KEY AUTOINCREMENT,\n'
  '  video_id TEXT,\n'
  '  step TEXT,\n'
  '  model TEXT,\n'
  '  prompt_version TEXT,\n'
  '  schema_version TEXT,\n'
  '  taxonomy_version TEXT,\n'
  '  started_at TEXT,\n'
  '  completed_at TEXT,\n'
  '  ok INTEGER,\n'
  '  detail TEXT\n'
  ')'),
 ('table',
  'renovation_project',
  'renovation_project',
  'CREATE TABLE renovation_project (project_id TEXT PRIMARY KEY, name TEXT NOT NULL,\n'
  "        profile_json TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 1,\n"
  '        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT)'),
 ('table',
  'source_attachment',
  'source_attachment',
  'CREATE TABLE source_attachment (attachment_id TEXT PRIMARY KEY, source_id TEXT NOT NULL '
  'REFERENCES source_record(source_id),\n'
  '        project_id TEXT NOT NULL REFERENCES renovation_project(project_id), sha256 TEXT, '
  'byte_size INTEGER,\n'
  '        media_type TEXT NOT NULL, original_name TEXT NOT NULL, storage_key TEXT NOT NULL '
  'UNIQUE,\n'
  "        state TEXT NOT NULL CHECK(state IN ('staging','ready','failed')), created_at TEXT NOT "
  'NULL,\n'
  '        operation_id TEXT NOT NULL UNIQUE, error TEXT)'),
 ('table',
  'source_record',
  'source_record',
  'CREATE TABLE source_record (source_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES '
  'renovation_project(project_id),\n'
  '        source_type TEXT NOT NULL CHECK(source_type IN '
  "('manual_note','standard_record','product_document','quote','site_photo')),\n"
  "        title TEXT NOT NULL, publisher TEXT NOT NULL DEFAULT '', document_no TEXT NOT NULL "
  "DEFAULT '',\n"
  "        edition TEXT NOT NULL DEFAULT '', published_on TEXT NOT NULL DEFAULT '', valid_from "
  "TEXT NOT NULL DEFAULT '',\n"
  "        valid_to TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '', locator TEXT "
  "NOT NULL DEFAULT '',\n"
  "        excerpt TEXT NOT NULL DEFAULT '', scope_json TEXT NOT NULL DEFAULT '{}',\n"
  "        verification_state TEXT NOT NULL DEFAULT 'unverified' CHECK(verification_state IN "
  "('unverified','user_checked')),\n"
  "        validity TEXT NOT NULL DEFAULT 'unknown' CHECK(validity IN "
  "('unknown','current','superseded','withdrawn')),\n"
  "        checked_at TEXT, check_note TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT "
  '1,\n'
  '        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'),
 ('table', 'sqlite_sequence', 'sqlite_sequence', 'CREATE TABLE sqlite_sequence(name,seq)'),
 ('table',
  'storage_artifact',
  'storage_artifact',
  'CREATE TABLE storage_artifact (artifact_id TEXT PRIMARY KEY NOT NULL,\n'
  '        namespace TEXT NOT NULL CHECK(namespace IN '
  "('sync_media','local_report','sync_report','checkpoint')),\n"
  '        root_id TEXT NOT NULL, root_device TEXT NOT NULL, root_inode TEXT NOT NULL,\n'
  '        storage_key TEXT NOT NULL, sha256 TEXT NOT NULL,\n'
  "        byte_size INTEGER NOT NULL CHECK(typeof(byte_size)='integer' AND byte_size>=0),\n"
  '        file_device TEXT NOT NULL, file_inode TEXT NOT NULL,\n'
  '        file_mtime_ns TEXT NOT NULL, file_ctime_ns TEXT NOT NULL,\n'
  '        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,\n'
  '        UNIQUE(namespace,root_id,storage_key))'),
 ('table',
  'storage_gc_item',
  'storage_gc_item',
  'CREATE TABLE storage_gc_item (operation_id TEXT NOT NULL REFERENCES '
  'storage_gc_operation(operation_id),\n'
  '        artifact_id TEXT NOT NULL REFERENCES storage_artifact(artifact_id),\n'
  '        namespace TEXT NOT NULL CHECK(namespace IN '
  "('sync_media','local_report','sync_report','checkpoint')),\n"
  '        root_id TEXT NOT NULL, storage_key TEXT NOT NULL, sha256 TEXT NOT NULL,\n'
  "        byte_size INTEGER NOT NULL CHECK(typeof(byte_size)='integer' AND byte_size>=0),\n"
  '        file_device TEXT NOT NULL, file_inode TEXT NOT NULL,\n'
  '        file_mtime_ns TEXT NOT NULL, file_ctime_ns TEXT NOT NULL,\n'
  '        state TEXT NOT NULL CHECK(state IN '
  "('pending','deleted','missing','retained','blocked')),\n"
  "        deleted_bytes INTEGER NOT NULL DEFAULT 0 CHECK(typeof(deleted_bytes)='integer' AND "
  'deleted_bytes>=0\n'
  "            AND deleted_bytes<=byte_size AND (state='deleted' OR deleted_bytes=0)),\n"
  '        reason_code TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(operation_id,artifact_id))'),
 ('table',
  'storage_gc_operation',
  'storage_gc_operation',
  'CREATE TABLE storage_gc_operation (operation_id TEXT PRIMARY KEY NOT NULL,\n'
  '        request_sha256 TEXT NOT NULL, preview_sha256 TEXT NOT NULL, roots_sha256 TEXT NOT '
  'NULL,\n'
  '        policy_json TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT,\n'
  "        state TEXT NOT NULL CHECK(state IN ('planned','running','completed','blocked')),\n"
  '        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'),
 ('table',
  'sync_checkpoint',
  'sync_checkpoint',
  'CREATE TABLE sync_checkpoint (checkpoint_id TEXT PRIMARY KEY,\n'
  '        publication_id TEXT NOT NULL UNIQUE REFERENCES sync_publication(publication_id),\n'
  '        receiver_version_json TEXT NOT NULL,\n'
  '        format_version INTEGER NOT NULL, schema_version INTEGER NOT NULL,\n'
  '        storage_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,\n'
  '        byte_size INTEGER NOT NULL, created_at TEXT NOT NULL)'),
 ('table',
  'sync_checkpoint_retirement',
  'sync_checkpoint_retirement',
  'CREATE TABLE sync_checkpoint_retirement (origin_kind TEXT NOT NULL CHECK(origin_kind IN '
  "('publication','recovery')),\n"
  '        checkpoint_id TEXT NOT NULL, operation_id TEXT NOT NULL REFERENCES '
  'storage_gc_operation(operation_id),\n'
  '        storage_key TEXT NOT NULL, sha256 TEXT NOT NULL,\n'
  "        byte_size INTEGER NOT NULL CHECK(typeof(byte_size)='integer' AND byte_size>0),\n"
  '        retired_at TEXT NOT NULL, PRIMARY KEY(origin_kind,checkpoint_id))'),
 ('table',
  'sync_pair',
  'sync_pair',
  'CREATE TABLE sync_pair (slot INTEGER PRIMARY KEY CHECK(slot=1),\n'
  '        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,\n'
  '        target_library_id TEXT NOT NULL, target_node_id TEXT NOT NULL,\n'
  '        protocol_version INTEGER NOT NULL, schema_fingerprint TEXT NOT NULL,\n'
  '        paired_at TEXT NOT NULL)'),
 ('table',
  'sync_publication',
  'sync_publication',
  'CREATE TABLE sync_publication (publication_id TEXT PRIMARY KEY,\n'
  '        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,\n'
  '        source_epoch INTEGER NOT NULL, source_revision INTEGER NOT NULL,\n'
  '        payload_sha256 TEXT NOT NULL, knowledge_sha256 TEXT NOT NULL,\n'
  '        committed_at TEXT NOT NULL, local_version_json TEXT NOT NULL)'),
 ('table',
  'sync_publication_fence',
  'sync_publication_fence',
  'CREATE TABLE sync_publication_fence (publication_id TEXT PRIMARY KEY REFERENCES '
  'sync_publication(publication_id),\n'
  '        generation INTEGER NOT NULL CHECK(generation>=0))'),
 ('table',
  'sync_published_video',
  'sync_published_video',
  'CREATE TABLE sync_published_video (publication_id TEXT NOT NULL, video_id TEXT NOT NULL,\n'
  '        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,\n'
  '        source_epoch INTEGER NOT NULL, source_revision INTEGER NOT NULL,\n'
  '        media_sha256 TEXT NOT NULL, committed_at TEXT NOT NULL,\n'
  '        PRIMARY KEY(publication_id,video_id))'),
 ('table',
  'sync_queue',
  'sync_queue',
  'CREATE TABLE sync_queue (\n'
  '  item_id INTEGER PRIMARY KEY AUTOINCREMENT,\n'
  '  url TEXT NOT NULL,\n'
  "  status TEXT DEFAULT 'queued',\n"
  '  attempts INTEGER DEFAULT 0,\n'
  '  lease_until TEXT,\n'
  '  last_error_class TEXT,\n'
  '  created_at TEXT,\n'
  '  updated_at TEXT,\n'
  '  owner_id TEXT,\n'
  '  owner_generation INTEGER NOT NULL DEFAULT 0,\n'
  '  lease_token TEXT,\n'
  '  lease_generation INTEGER NOT NULL DEFAULT 0,\n'
  '  retry_at TEXT,\n'
  '  video_id TEXT,\n'
  '  publication_id TEXT,\n'
  '  result_hash TEXT,\n'
  '  budget_wait_reason TEXT,\n'
  '  budget_operation_id TEXT\n'
  ')'),
 ('table',
  'sync_queue_budget_receipt',
  'sync_queue_budget_receipt',
  'CREATE TABLE sync_queue_budget_receipt (request_id TEXT PRIMARY KEY,\n'
  '        request_sha256 TEXT NOT NULL, request_json TEXT NOT NULL,\n'
  "        action TEXT NOT NULL CHECK(action IN ('budget_defer','budget_resume')),\n"
  '        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,\n'
  '        target_library_id TEXT NOT NULL, target_node_id TEXT NOT NULL,\n'
  '        receiver_library_epoch INTEGER NOT NULL CHECK(receiver_library_epoch>=1),\n'
  '        recovery_generation INTEGER NOT NULL CHECK(recovery_generation>=0),\n'
  '        item_id INTEGER NOT NULL REFERENCES sync_queue(item_id),\n'
  '        lease_generation INTEGER NOT NULL CHECK(lease_generation>=1),\n'
  '        result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL, committed_at TEXT NOT NULL,\n'
  '        CHECK(length(CAST(request_json AS BLOB))<=16384),\n'
  '        CHECK(length(CAST(result_json AS BLOB))<=16384))'),
 ('table',
  'sync_queue_publication',
  'sync_queue_publication',
  'CREATE TABLE sync_queue_publication (item_id INTEGER NOT NULL REFERENCES sync_queue(item_id),\n'
  '        lease_generation INTEGER NOT NULL, lease_token TEXT NOT NULL,\n'
  '        owner_id TEXT NOT NULL, owner_generation INTEGER NOT NULL,\n'
  '        publication_id TEXT NOT NULL, video_id TEXT NOT NULL, created_at TEXT NOT NULL,\n'
  '        PRIMARY KEY(item_id,lease_generation),\n'
  '        FOREIGN KEY(publication_id,video_id)\n'
  '            REFERENCES sync_published_video(publication_id,video_id))'),
 ('table',
  'sync_queue_source_receipt',
  'sync_queue_source_receipt',
  'CREATE TABLE sync_queue_source_receipt (request_id TEXT PRIMARY KEY,\n'
  '        request_sha256 TEXT NOT NULL, request_json TEXT NOT NULL,\n'
  "        action TEXT NOT NULL CHECK(action='source_fail'),\n"
  '        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,\n'
  '        target_library_id TEXT NOT NULL, target_node_id TEXT NOT NULL,\n'
  '        receiver_library_epoch INTEGER NOT NULL CHECK(receiver_library_epoch>=1),\n'
  '        recovery_generation INTEGER NOT NULL CHECK(recovery_generation>=0),\n'
  '        item_id INTEGER NOT NULL REFERENCES sync_queue(item_id),\n'
  '        lease_generation INTEGER NOT NULL CHECK(lease_generation>=1),\n'
  '        core_sha256 TEXT NOT NULL, submitted_url_sha256 TEXT NOT NULL, failure_sha256 TEXT NOT '
  'NULL,\n'
  '        result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL, committed_at TEXT NOT NULL,\n'
  '        CHECK(length(CAST(request_json AS BLOB))<=16384),\n'
  '        CHECK(length(CAST(result_json AS BLOB))<=16384))'),
 ('table',
  'sync_recovery_checkpoint',
  'sync_recovery_checkpoint',
  'CREATE TABLE sync_recovery_checkpoint (checkpoint_id TEXT PRIMARY KEY,\n'
  '        operation_id TEXT NOT NULL UNIQUE REFERENCES sync_recovery_operation(operation_id),\n'
  "        origin_kind TEXT NOT NULL CHECK(origin_kind='recovery'),\n"
  '        receiver_version_json TEXT NOT NULL,\n'
  '        format_version INTEGER NOT NULL, schema_version INTEGER NOT NULL,\n'
  '        storage_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,\n'
  '        byte_size INTEGER NOT NULL, created_at TEXT NOT NULL)'),
 ('table',
  'sync_recovery_control',
  'sync_recovery_control',
  'CREATE TABLE sync_recovery_control (slot INTEGER PRIMARY KEY CHECK(slot=1),\n'
  "        state TEXT NOT NULL CHECK(state IN ('running','held','resume_pending')),\n"
  '        generation INTEGER NOT NULL CHECK(generation>=0),\n'
  '        revision INTEGER NOT NULL CHECK(revision>=0), source_floor_json TEXT,\n'
  '        restore_operation_id TEXT REFERENCES sync_recovery_operation(operation_id),\n'
  '        updated_at TEXT NOT NULL)'),
 ('table',
  'sync_recovery_operation',
  'sync_recovery_operation',
  'CREATE TABLE sync_recovery_operation (operation_id TEXT PRIMARY KEY,\n'
  "        action TEXT NOT NULL CHECK(action IN ('restore','resume')),\n"
  '        request_sha256 TEXT NOT NULL, request_json TEXT NOT NULL,\n'
  '        result_json TEXT NOT NULL, created_at TEXT NOT NULL)'),
 ('table',
  'sync_report',
  'sync_report',
  'CREATE TABLE sync_report (name TEXT PRIMARY KEY,\n'
  '        publication_id TEXT NOT NULL REFERENCES sync_publication(publication_id),\n'
  '        storage_key TEXT NOT NULL, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL,\n'
  '        policy_version TEXT NOT NULL, source_version_json TEXT NOT NULL,\n'
  '        local_version_json TEXT NOT NULL, generated_at TEXT NOT NULL,\n'
  '        review_events_sha256 TEXT NOT NULL)'),
 ('table',
  'sync_session',
  'sync_session',
  'CREATE TABLE sync_session (slot INTEGER PRIMARY KEY REFERENCES sync_pair(slot),\n'
  '        session_id TEXT NOT NULL, lease_token TEXT NOT NULL, generation INTEGER NOT NULL,\n'
  '        lease_until TEXT NOT NULL, updated_at TEXT NOT NULL,\n'
  "        metrics_json TEXT NOT NULL DEFAULT '{}')"),
 ('table',
  'sync_source_admission',
  'sync_source_admission',
  'CREATE TABLE sync_source_admission (admission_id TEXT NOT NULL '
  "CHECK(typeof(admission_id)='text' AND length(admission_id)=36 AND substr(admission_id,9,1)='-' "
  "AND substr(admission_id,14,1)='-' AND substr(admission_id,19,1)='-' AND "
  "substr(admission_id,24,1)='-' AND length(replace(admission_id,'-',''))=32 AND "
  "replace(admission_id,'-','') NOT GLOB '*[^0-9a-f]*') PRIMARY KEY,\n"
  "format_version INTEGER NOT NULL CHECK(typeof(format_version)='integer' AND format_version "
  'BETWEEN 1 AND 2),\n'
  "source_library_id TEXT NOT NULL CHECK(typeof(source_library_id)='text' AND "
  "length(source_library_id)=36 AND substr(source_library_id,9,1)='-' AND "
  "substr(source_library_id,14,1)='-' AND substr(source_library_id,19,1)='-' AND "
  "substr(source_library_id,24,1)='-' AND length(replace(source_library_id,'-',''))=32 AND "
  "replace(source_library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "source_node_id TEXT NOT NULL CHECK(typeof(source_node_id)='text' AND length(source_node_id)=36 "
  "AND substr(source_node_id,9,1)='-' AND substr(source_node_id,14,1)='-' AND "
  "substr(source_node_id,19,1)='-' AND substr(source_node_id,24,1)='-' AND "
  "length(replace(source_node_id,'-',''))=32 AND replace(source_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*'),\n"
  "target_library_id TEXT NOT NULL CHECK(typeof(target_library_id)='text' AND "
  "length(target_library_id)=36 AND substr(target_library_id,9,1)='-' AND "
  "substr(target_library_id,14,1)='-' AND substr(target_library_id,19,1)='-' AND "
  "substr(target_library_id,24,1)='-' AND length(replace(target_library_id,'-',''))=32 AND "
  "replace(target_library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "target_node_id TEXT NOT NULL CHECK(typeof(target_node_id)='text' AND length(target_node_id)=36 "
  "AND substr(target_node_id,9,1)='-' AND substr(target_node_id,14,1)='-' AND "
  "substr(target_node_id,19,1)='-' AND substr(target_node_id,24,1)='-' AND "
  "length(replace(target_node_id,'-',''))=32 AND replace(target_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*'),\n"
  "core_sha256 TEXT NOT NULL CHECK(typeof(core_sha256)='text' AND length(core_sha256)=64 AND "
  "core_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "item_id INTEGER NOT NULL CHECK(typeof(item_id)='integer' AND item_id BETWEEN 1 AND "
  '9223372036854775807),\n'
  "submitted_url_sha256 TEXT NOT NULL CHECK(typeof(submitted_url_sha256)='text' AND "
  "length(submitted_url_sha256)=64 AND submitted_url_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "current_home_epoch INTEGER NOT NULL CHECK(typeof(current_home_epoch)='integer' AND "
  'current_home_epoch BETWEEN 1 AND 9223372036854775807),\n'
  "receiver_library_epoch INTEGER NOT NULL CHECK(typeof(receiver_library_epoch)='integer' AND "
  'receiver_library_epoch BETWEEN 1 AND 9223372036854775807),\n'
  "recovery_generation INTEGER NOT NULL CHECK(typeof(recovery_generation)='integer' AND "
  'recovery_generation BETWEEN 0 AND 9223372036854775807),\n'
  "control_revision INTEGER NOT NULL CHECK(typeof(control_revision)='integer' AND control_revision "
  'BETWEEN 0 AND 9223372036854775807),\n'
  'previous_admission_id TEXT CHECK(previous_admission_id IS NULL OR '
  "(typeof(previous_admission_id)='text' AND length(previous_admission_id)=36 AND "
  "substr(previous_admission_id,9,1)='-' AND substr(previous_admission_id,14,1)='-' AND "
  "substr(previous_admission_id,19,1)='-' AND substr(previous_admission_id,24,1)='-' AND "
  "length(replace(previous_admission_id,'-',''))=32 AND replace(previous_admission_id,'-','') NOT "
  "GLOB '*[^0-9a-f]*')),\n"
  "lease_generation INTEGER NOT NULL CHECK(typeof(lease_generation)='integer' AND lease_generation "
  'BETWEEN 1 AND 9223372036854775807),\n'
  "attempts INTEGER NOT NULL CHECK(typeof(attempts)='integer' AND attempts BETWEEN 1 AND 5),\n"
  "attempt_basis TEXT NOT NULL CHECK(attempt_basis IN ('held_claim','original_defer_refund')),\n"
  "request_json TEXT NOT NULL CHECK(typeof(request_json)='text' AND length(CAST(request_json AS "
  'BLOB)) BETWEEN 2 AND 16384 AND json_valid(request_json) AND CASE WHEN json_valid(request_json) '
  "THEN json_type(request_json)='object' ELSE 0 END),\n"
  "request_sha256 TEXT NOT NULL CHECK(typeof(request_sha256)='text' AND length(request_sha256)=64 "
  "AND request_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "admission_sha256 TEXT NOT NULL CHECK(typeof(admission_sha256)='text' AND "
  "length(admission_sha256)=64 AND admission_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "result_json TEXT NOT NULL CHECK(typeof(result_json)='text' AND length(CAST(result_json AS "
  'BLOB)) BETWEEN 2 AND 16384 AND json_valid(result_json) AND CASE WHEN json_valid(result_json) '
  "THEN json_type(result_json)='object' ELSE 0 END),\n"
  "result_sha256 TEXT NOT NULL CHECK(typeof(result_sha256)='text' AND length(result_sha256)=64 AND "
  "result_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "committed_at TEXT NOT NULL CHECK(typeof(committed_at)='text' AND length(committed_at)=24 AND "
  'committed_at GLOB '
  "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),\n"
  'CHECK(source_library_id!=target_library_id AND source_node_id!=target_node_id),\n'
  'FOREIGN KEY(item_id) REFERENCES sync_queue(item_id),\n'
  'FOREIGN KEY(previous_admission_id) REFERENCES sync_source_admission(admission_id),\n'
  'CHECK(previous_admission_id IS NULL OR previous_admission_id!=admission_id))'),
 ('table',
  'sync_source_continuity_activation',
  'sync_source_continuity_activation',
  'CREATE TABLE sync_source_continuity_activation (continuity_id TEXT NOT NULL '
  "CHECK(typeof(continuity_id)='text' AND length(continuity_id)=36 AND "
  "substr(continuity_id,9,1)='-' AND substr(continuity_id,14,1)='-' AND "
  "substr(continuity_id,19,1)='-' AND substr(continuity_id,24,1)='-' AND "
  "length(replace(continuity_id,'-',''))=32 AND replace(continuity_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*') PRIMARY KEY,\n"
  "format_version INTEGER NOT NULL CHECK(typeof(format_version)='integer' AND format_version "
  'BETWEEN 1 AND 1),\n'
  "owner_profile_sha256 TEXT NOT NULL CHECK(typeof(owner_profile_sha256)='text' AND "
  "length(owner_profile_sha256)=64 AND owner_profile_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "source_library_id TEXT NOT NULL CHECK(typeof(source_library_id)='text' AND "
  "length(source_library_id)=36 AND substr(source_library_id,9,1)='-' AND "
  "substr(source_library_id,14,1)='-' AND substr(source_library_id,19,1)='-' AND "
  "substr(source_library_id,24,1)='-' AND length(replace(source_library_id,'-',''))=32 AND "
  "replace(source_library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "source_node_id TEXT NOT NULL CHECK(typeof(source_node_id)='text' AND length(source_node_id)=36 "
  "AND substr(source_node_id,9,1)='-' AND substr(source_node_id,14,1)='-' AND "
  "substr(source_node_id,19,1)='-' AND substr(source_node_id,24,1)='-' AND "
  "length(replace(source_node_id,'-',''))=32 AND replace(source_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*'),\n"
  "target_library_id TEXT NOT NULL CHECK(typeof(target_library_id)='text' AND "
  "length(target_library_id)=36 AND substr(target_library_id,9,1)='-' AND "
  "substr(target_library_id,14,1)='-' AND substr(target_library_id,19,1)='-' AND "
  "substr(target_library_id,24,1)='-' AND length(replace(target_library_id,'-',''))=32 AND "
  "replace(target_library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "target_node_id TEXT NOT NULL CHECK(typeof(target_node_id)='text' AND length(target_node_id)=36 "
  "AND substr(target_node_id,9,1)='-' AND substr(target_node_id,14,1)='-' AND "
  "substr(target_node_id,19,1)='-' AND substr(target_node_id,24,1)='-' AND "
  "length(replace(target_node_id,'-',''))=32 AND replace(target_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*'),\n"
  "target_epoch INTEGER NOT NULL CHECK(typeof(target_epoch)='integer' AND target_epoch BETWEEN 1 "
  'AND 9223372036854775807),\n'
  "contract_json TEXT NOT NULL CHECK(typeof(contract_json)='text' AND length(CAST(contract_json AS "
  'BLOB)) BETWEEN 2 AND 4096 AND json_valid(contract_json) AND CASE WHEN json_valid(contract_json) '
  "THEN json_type(contract_json)='object' ELSE 0 END),\n"
  "contract_sha256 TEXT NOT NULL CHECK(typeof(contract_sha256)='text' AND "
  "length(contract_sha256)=64 AND contract_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "activated_at TEXT NOT NULL CHECK(typeof(activated_at)='text' AND length(activated_at)=24 AND "
  'activated_at GLOB '
  "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),\n"
  'CHECK(source_library_id!=target_library_id AND source_node_id!=target_node_id))'),
 ('table',
  'sync_source_control_disposition',
  'sync_source_control_disposition',
  'CREATE TABLE sync_source_control_disposition (request_id TEXT NOT NULL '
  "CHECK(typeof(request_id)='text' AND length(request_id)=36 AND substr(request_id,9,1)='-' AND "
  "substr(request_id,14,1)='-' AND substr(request_id,19,1)='-' AND substr(request_id,24,1)='-' AND "
  "length(replace(request_id,'-',''))=32 AND replace(request_id,'-','') NOT GLOB '*[^0-9a-f]*') "
  'PRIMARY KEY,\n'
  "format_version INTEGER NOT NULL CHECK(typeof(format_version)='integer' AND format_version "
  'BETWEEN 1 AND 2),\n'
  "source_library_id TEXT NOT NULL CHECK(typeof(source_library_id)='text' AND "
  "length(source_library_id)=36 AND substr(source_library_id,9,1)='-' AND "
  "substr(source_library_id,14,1)='-' AND substr(source_library_id,19,1)='-' AND "
  "substr(source_library_id,24,1)='-' AND length(replace(source_library_id,'-',''))=32 AND "
  "replace(source_library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "source_node_id TEXT NOT NULL CHECK(typeof(source_node_id)='text' AND length(source_node_id)=36 "
  "AND substr(source_node_id,9,1)='-' AND substr(source_node_id,14,1)='-' AND "
  "substr(source_node_id,19,1)='-' AND substr(source_node_id,24,1)='-' AND "
  "length(replace(source_node_id,'-',''))=32 AND replace(source_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*'),\n"
  "target_library_id TEXT NOT NULL CHECK(typeof(target_library_id)='text' AND "
  "length(target_library_id)=36 AND substr(target_library_id,9,1)='-' AND "
  "substr(target_library_id,14,1)='-' AND substr(target_library_id,19,1)='-' AND "
  "substr(target_library_id,24,1)='-' AND length(replace(target_library_id,'-',''))=32 AND "
  "replace(target_library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "target_node_id TEXT NOT NULL CHECK(typeof(target_node_id)='text' AND length(target_node_id)=36 "
  "AND substr(target_node_id,9,1)='-' AND substr(target_node_id,14,1)='-' AND "
  "substr(target_node_id,19,1)='-' AND substr(target_node_id,24,1)='-' AND "
  "length(replace(target_node_id,'-',''))=32 AND replace(target_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*'),\n"
  "core_sha256 TEXT NOT NULL CHECK(typeof(core_sha256)='text' AND length(core_sha256)=64 AND "
  "core_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "item_id INTEGER NOT NULL CHECK(typeof(item_id)='integer' AND item_id BETWEEN 1 AND "
  '9223372036854775807),\n'
  "original_request_id TEXT NOT NULL CHECK(typeof(original_request_id)='text' AND "
  "length(original_request_id)=36 AND substr(original_request_id,9,1)='-' AND "
  "substr(original_request_id,14,1)='-' AND substr(original_request_id,19,1)='-' AND "
  "substr(original_request_id,24,1)='-' AND length(replace(original_request_id,'-',''))=32 AND "
  "replace(original_request_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "original_request_sha256 TEXT NOT NULL CHECK(typeof(original_request_sha256)='text' AND "
  "length(original_request_sha256)=64 AND original_request_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  'original_kind TEXT NOT NULL CHECK(original_kind IN '
  "('budget_defer','budget_resume','source_fail')),\n"
  "original_request_json TEXT NOT NULL CHECK(typeof(original_request_json)='text' AND "
  'length(CAST(original_request_json AS BLOB)) BETWEEN 2 AND 16384 AND '
  'json_valid(original_request_json) AND CASE WHEN json_valid(original_request_json) THEN '
  "json_type(original_request_json)='object' ELSE 0 END),\n"
  "continuity_id TEXT CHECK(continuity_id IS NULL OR (typeof(continuity_id)='text' AND "
  "length(continuity_id)=36 AND substr(continuity_id,9,1)='-' AND substr(continuity_id,14,1)='-' "
  "AND substr(continuity_id,19,1)='-' AND substr(continuity_id,24,1)='-' AND "
  "length(replace(continuity_id,'-',''))=32 AND replace(continuity_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*')),\n"
  'registration_sha256 TEXT CHECK(registration_sha256 IS NULL OR '
  "(typeof(registration_sha256)='text' AND length(registration_sha256)=64 AND registration_sha256 "
  "NOT GLOB '*[^0-9a-f]*')),\n"
  "disposition TEXT NOT NULL CHECK(disposition IN ('applied','not_applied')),\n"
  "request_json TEXT NOT NULL CHECK(typeof(request_json)='text' AND length(CAST(request_json AS "
  'BLOB)) BETWEEN 2 AND 32768 AND json_valid(request_json) AND CASE WHEN json_valid(request_json) '
  "THEN json_type(request_json)='object' ELSE 0 END),\n"
  "request_sha256 TEXT NOT NULL CHECK(typeof(request_sha256)='text' AND length(request_sha256)=64 "
  "AND request_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "result_json TEXT NOT NULL CHECK(typeof(result_json)='text' AND length(CAST(result_json AS "
  'BLOB)) BETWEEN 2 AND 32768 AND json_valid(result_json) AND CASE WHEN json_valid(result_json) '
  "THEN json_type(result_json)='object' ELSE 0 END),\n"
  "result_sha256 TEXT NOT NULL CHECK(typeof(result_sha256)='text' AND length(result_sha256)=64 AND "
  "result_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "disposition_sha256 TEXT NOT NULL CHECK(typeof(disposition_sha256)='text' AND "
  "length(disposition_sha256)=64 AND disposition_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "defer_receipt_json TEXT CHECK(defer_receipt_json IS NULL OR (typeof(defer_receipt_json)='text' "
  'AND length(CAST(defer_receipt_json AS BLOB)) BETWEEN 2 AND 16384 AND '
  'json_valid(defer_receipt_json) AND CASE WHEN json_valid(defer_receipt_json) THEN '
  "json_type(defer_receipt_json)='object' ELSE 0 END)),\n"
  'defer_receipt_sha256 TEXT CHECK(defer_receipt_sha256 IS NULL OR '
  "(typeof(defer_receipt_sha256)='text' AND length(defer_receipt_sha256)=64 AND "
  "defer_receipt_sha256 NOT GLOB '*[^0-9a-f]*')),\n"
  "committed_at TEXT NOT NULL CHECK(typeof(committed_at)='text' AND length(committed_at)=24 AND "
  'committed_at GLOB '
  "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),\n"
  'CHECK(source_library_id!=target_library_id AND source_node_id!=target_node_id),\n'
  'CHECK(request_id!=original_request_id),\n'
  'FOREIGN KEY(continuity_id) REFERENCES sync_source_continuity_activation(continuity_id),\n'
  'FOREIGN KEY(registration_sha256) REFERENCES '
  'sync_source_control_registration(registration_sha256),\n'
  'FOREIGN KEY(item_id) REFERENCES sync_queue(item_id),\n'
  'CHECK((continuity_id IS NULL)=(registration_sha256 IS NULL)),\n'
  "CHECK(disposition!='not_applied' OR continuity_id IS NOT NULL),\n"
  'CHECK((defer_receipt_json IS NULL)=(defer_receipt_sha256 IS NULL)),\n'
  "CHECK((disposition='applied' AND original_kind='budget_resume')=(defer_receipt_json IS NOT "
  'NULL)))'),
 ('table',
  'sync_source_control_registration',
  'sync_source_control_registration',
  'CREATE TABLE sync_source_control_registration (request_id TEXT NOT NULL '
  "CHECK(typeof(request_id)='text' AND length(request_id)=36 AND substr(request_id,9,1)='-' AND "
  "substr(request_id,14,1)='-' AND substr(request_id,19,1)='-' AND substr(request_id,24,1)='-' AND "
  "length(replace(request_id,'-',''))=32 AND replace(request_id,'-','') NOT GLOB '*[^0-9a-f]*') "
  'PRIMARY KEY,\n'
  "format_version INTEGER NOT NULL CHECK(typeof(format_version)='integer' AND format_version "
  'BETWEEN 1 AND 1),\n'
  "source_library_id TEXT NOT NULL CHECK(typeof(source_library_id)='text' AND "
  "length(source_library_id)=36 AND substr(source_library_id,9,1)='-' AND "
  "substr(source_library_id,14,1)='-' AND substr(source_library_id,19,1)='-' AND "
  "substr(source_library_id,24,1)='-' AND length(replace(source_library_id,'-',''))=32 AND "
  "replace(source_library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "source_node_id TEXT NOT NULL CHECK(typeof(source_node_id)='text' AND length(source_node_id)=36 "
  "AND substr(source_node_id,9,1)='-' AND substr(source_node_id,14,1)='-' AND "
  "substr(source_node_id,19,1)='-' AND substr(source_node_id,24,1)='-' AND "
  "length(replace(source_node_id,'-',''))=32 AND replace(source_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*'),\n"
  "target_library_id TEXT NOT NULL CHECK(typeof(target_library_id)='text' AND "
  "length(target_library_id)=36 AND substr(target_library_id,9,1)='-' AND "
  "substr(target_library_id,14,1)='-' AND substr(target_library_id,19,1)='-' AND "
  "substr(target_library_id,24,1)='-' AND length(replace(target_library_id,'-',''))=32 AND "
  "replace(target_library_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "target_node_id TEXT NOT NULL CHECK(typeof(target_node_id)='text' AND length(target_node_id)=36 "
  "AND substr(target_node_id,9,1)='-' AND substr(target_node_id,14,1)='-' AND "
  "substr(target_node_id,19,1)='-' AND substr(target_node_id,24,1)='-' AND "
  "length(replace(target_node_id,'-',''))=32 AND replace(target_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*'),\n"
  "continuity_id TEXT NOT NULL CHECK(typeof(continuity_id)='text' AND length(continuity_id)=36 AND "
  "substr(continuity_id,9,1)='-' AND substr(continuity_id,14,1)='-' AND "
  "substr(continuity_id,19,1)='-' AND substr(continuity_id,24,1)='-' AND "
  "length(replace(continuity_id,'-',''))=32 AND replace(continuity_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*'),\n"
  "core_sha256 TEXT NOT NULL CHECK(typeof(core_sha256)='text' AND length(core_sha256)=64 AND "
  "core_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "item_id INTEGER NOT NULL CHECK(typeof(item_id)='integer' AND item_id BETWEEN 1 AND "
  '9223372036854775807),\n'
  "original_request_id TEXT NOT NULL CHECK(typeof(original_request_id)='text' AND "
  "length(original_request_id)=36 AND substr(original_request_id,9,1)='-' AND "
  "substr(original_request_id,14,1)='-' AND substr(original_request_id,19,1)='-' AND "
  "substr(original_request_id,24,1)='-' AND length(replace(original_request_id,'-',''))=32 AND "
  "replace(original_request_id,'-','') NOT GLOB '*[^0-9a-f]*'),\n"
  "original_request_sha256 TEXT NOT NULL CHECK(typeof(original_request_sha256)='text' AND "
  "length(original_request_sha256)=64 AND original_request_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  'original_kind TEXT NOT NULL CHECK(original_kind IN '
  "('budget_defer','budget_resume','source_fail')),\n"
  "original_request_json TEXT NOT NULL CHECK(typeof(original_request_json)='text' AND "
  'length(CAST(original_request_json AS BLOB)) BETWEEN 2 AND 16384 AND '
  'json_valid(original_request_json) AND CASE WHEN json_valid(original_request_json) THEN '
  "json_type(original_request_json)='object' ELSE 0 END),\n"
  "request_json TEXT NOT NULL CHECK(typeof(request_json)='text' AND length(CAST(request_json AS "
  'BLOB)) BETWEEN 2 AND 32768 AND json_valid(request_json) AND CASE WHEN json_valid(request_json) '
  "THEN json_type(request_json)='object' ELSE 0 END),\n"
  "request_sha256 TEXT NOT NULL CHECK(typeof(request_sha256)='text' AND length(request_sha256)=64 "
  "AND request_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "registration_sha256 TEXT NOT NULL CHECK(typeof(registration_sha256)='text' AND "
  "length(registration_sha256)=64 AND registration_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "result_json TEXT NOT NULL CHECK(typeof(result_json)='text' AND length(CAST(result_json AS "
  'BLOB)) BETWEEN 2 AND 32768 AND json_valid(result_json) AND CASE WHEN json_valid(result_json) '
  "THEN json_type(result_json)='object' ELSE 0 END),\n"
  "result_sha256 TEXT NOT NULL CHECK(typeof(result_sha256)='text' AND length(result_sha256)=64 AND "
  "result_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "committed_at TEXT NOT NULL CHECK(typeof(committed_at)='text' AND length(committed_at)=24 AND "
  'committed_at GLOB '
  "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),\n"
  'CHECK(source_library_id!=target_library_id AND source_node_id!=target_node_id),\n'
  'CHECK(request_id!=original_request_id),\n'
  'FOREIGN KEY(continuity_id) REFERENCES sync_source_continuity_activation(continuity_id),\n'
  'FOREIGN KEY(item_id) REFERENCES sync_queue(item_id))'),
 ('table',
  'sync_upload_pin',
  'sync_upload_pin',
  'CREATE TABLE sync_upload_pin (pin_id TEXT PRIMARY KEY NOT NULL,\n'
  "        kind TEXT NOT NULL CHECK(kind IN ('publication','media','legacy')),\n"
  '        publication_id TEXT, parent_pin_id TEXT REFERENCES sync_upload_pin(pin_id),\n'
  '        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,\n'
  '        source_epoch INTEGER, source_revision INTEGER,\n'
  '        target_library_id TEXT, target_library_epoch INTEGER, recovery_generation INTEGER,\n'
  '        descriptor_json TEXT, intent_sha256 TEXT NOT NULL,\n'
  '        state TEXT NOT NULL CHECK(state IN '
  "('reserved','stored','published','abandoned','legacy_hold')),\n"
  '        artifact_id TEXT REFERENCES storage_artifact(artifact_id),\n'
  '        abandon_operation_id TEXT, abandon_request_sha256 TEXT,\n'
  '        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,\n'
  '        CHECK((abandon_operation_id IS NULL AND abandon_request_sha256 IS NULL)\n'
  "           OR (kind='publication' AND state='abandoned' AND abandon_operation_id IS NOT NULL\n"
  '               AND abandon_request_sha256 IS NOT NULL)),\n'
  "        CHECK((kind='publication' AND publication_id IS NOT NULL AND parent_pin_id IS NULL\n"
  '                AND descriptor_json IS NULL AND artifact_id IS NULL\n'
  "                AND state IN ('reserved','published','abandoned'))\n"
  "           OR (kind='media' AND publication_id IS NOT NULL AND parent_pin_id IS NOT NULL\n"
  '                AND descriptor_json IS NOT NULL AND state IN '
  "('reserved','stored','published','abandoned')\n"
  "                AND (state NOT IN ('stored','published') OR artifact_id IS NOT NULL))\n"
  "           OR (kind='legacy' AND publication_id IS NULL AND parent_pin_id IS NULL\n"
  "                AND descriptor_json IS NOT NULL AND state='legacy_hold')),\n"
  "        CHECK((kind='legacy' AND source_epoch IS NULL AND source_revision IS NULL\n"
  '                AND target_library_id IS NULL AND target_library_epoch IS NULL AND '
  'recovery_generation IS NULL)\n'
  "           OR (kind IN ('publication','media') AND typeof(source_epoch)='integer' AND "
  'source_epoch>=1\n'
  "                AND typeof(source_revision)='integer' AND source_revision>=0\n"
  "                AND target_library_id IS NOT NULL AND typeof(target_library_epoch)='integer'\n"
  "                AND target_library_epoch>=1 AND typeof(recovery_generation)='integer' AND "
  'recovery_generation>=0)))'),
 ('table',
  'task_request',
  'task_request',
  'CREATE TABLE task_request (\n'
  '  request_id TEXT PRIMARY KEY,\n'
  '  job_id INTEGER NOT NULL REFERENCES job(job_id),\n'
  '  payload_signature TEXT NOT NULL,\n'
  '  created_at TEXT NOT NULL\n'
  ')'),
 ('table',
  'transcript_segment',
  'transcript_segment',
  'CREATE TABLE transcript_segment (\n'
  '  id TEXT NOT NULL,\n'
  '  video_id TEXT,\n'
  '  idx INTEGER,\n'
  '  start_ms INTEGER,\n'
  '  end_ms INTEGER,\n'
  '  text TEXT,\n'
  '  PRIMARY KEY (video_id, id)\n'
  ')'),
 ('table',
  'user_decision',
  'user_decision',
  'CREATE TABLE user_decision (\n'
  '  decision_id INTEGER PRIMARY KEY AUTOINCREMENT,\n'
  '  atom_id TEXT,\n'
  '  conflict_id TEXT,\n'
  '  action TEXT,\n'
  '  revised_claim TEXT,\n'
  '  note TEXT,\n'
  '  created_at TEXT,\n'
  '  event_id TEXT,\n'
  '  client_id TEXT,\n'
  '  decided_at TEXT,\n'
  '  target_fingerprint TEXT,\n'
  '  target_snapshot_json TEXT,\n'
  '  event_schema_version INTEGER\n'
  ')'),
 ('table',
  'video_asset',
  'video_asset',
  'CREATE TABLE video_asset (\n'
  '  video_id TEXT PRIMARY KEY,\n'
  '  source_platform TEXT,\n'
  '  source_type TEXT,\n'
  '  source_url TEXT,\n'
  '  title TEXT,\n'
  '  author TEXT,\n'
  '  source_published_at TEXT,\n'
  '  duration_ms INTEGER,\n'
  '  width INTEGER,\n'
  '  height INTEGER,\n'
  '  fps REAL,\n'
  '  content_sha256 TEXT UNIQUE,\n'
  '  audio_sha256 TEXT,\n'
  '  imported_at TEXT,\n'
  '  files_json TEXT,\n'
  "  status TEXT DEFAULT 'imported',\n"
  "  sync_state TEXT NOT NULL DEFAULT 'ready'\n"
  ')'),
 ('table',
  'visual_observation',
  'visual_observation',
  'CREATE TABLE visual_observation (\n'
  '  id TEXT NOT NULL,\n'
  '  video_id TEXT,\n'
  '  frame_id TEXT,\n'
  '  pts_ms INTEGER,\n'
  '  scene_description TEXT,\n'
  '  materials_json TEXT,\n'
  '  tools_json TEXT,\n'
  '  subtitle_text TEXT,\n'
  '  measurement TEXT,\n'
  '  PRIMARY KEY (video_id, id)\n'
  ')'),
 ('table',
  'worker_lease',
  'worker_lease',
  'CREATE TABLE worker_lease (\n'
  '  scope TEXT PRIMARY KEY,\n'
  '  owner_id TEXT,\n'
  '  lease_until INTEGER NOT NULL DEFAULT 0,\n'
  '  updated_at TEXT\n'
  ')'),
 ('trigger',
  'home_recovery_no_delete',
  'home_knowledge_recovery_operation',
  'CREATE TRIGGER home_recovery_no_delete BEFORE DELETE ON home_knowledge_recovery_operation BEGIN '
  "SELECT RAISE(ABORT,'HOME_RECOVERY_IMMUTABLE'); END"),
 ('trigger',
  'home_recovery_no_update',
  'home_knowledge_recovery_operation',
  'CREATE TRIGGER home_recovery_no_update BEFORE UPDATE ON home_knowledge_recovery_operation BEGIN '
  "SELECT RAISE(ABORT,'HOME_RECOVERY_IMMUTABLE'); END"),
 ('trigger',
  'kb_atom_delete',
  'knowledge_atom',
  'CREATE TRIGGER kb_atom_delete AFTER DELETE ON knowledge_atom BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_atom_insert',
  'knowledge_atom',
  'CREATE TRIGGER kb_atom_insert AFTER INSERT ON knowledge_atom BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_atom_update',
  'knowledge_atom',
  'CREATE TRIGGER kb_atom_update AFTER UPDATE ON knowledge_atom\n'
  'WHEN OLD.id IS NOT NEW.id OR OLD.video_id IS NOT NEW.video_id\n'
  '  OR OLD.category IS NOT NEW.category OR OLD.stage IS NOT NEW.stage\n'
  '  OR OLD.space IS NOT NEW.space OR OLD.subject IS NOT NEW.subject\n'
  '  OR OLD.claim IS NOT NEW.claim OR OLD.reason IS NOT NEW.reason\n'
  '  OR OLD.risk_if_ignored IS NOT NEW.risk_if_ignored OR OLD.polarity IS NOT NEW.polarity\n'
  '  OR OLD.conditions_json IS NOT NEW.conditions_json OR OLD.parameters_json IS NOT '
  'NEW.parameters_json\n'
  '  OR OLD.confidence IS NOT NEW.confidence OR OLD.status IS NOT NEW.status\n'
  '  OR OLD.cluster_id IS NOT NEW.cluster_id OR OLD.dimension IS NOT NEW.dimension\n'
  '  OR OLD.evidence_nature IS NOT NEW.evidence_nature OR OLD.exceptions IS NOT NEW.exceptions\n'
  '  OR OLD.prices_json IS NOT NEW.prices_json\n'
  'BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_cluster_delete',
  'knowledge_cluster',
  'CREATE TRIGGER kb_cluster_delete AFTER DELETE ON knowledge_cluster BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_cluster_insert',
  'knowledge_cluster',
  'CREATE TRIGGER kb_cluster_insert AFTER INSERT ON knowledge_cluster BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_cluster_update',
  'knowledge_cluster',
  'CREATE TRIGGER kb_cluster_update AFTER UPDATE ON knowledge_cluster\n'
  'WHEN OLD.cluster_id IS NOT NEW.cluster_id OR OLD.canonical_topic IS NOT NEW.canonical_topic\n'
  '  OR OLD.relation IS NOT NEW.relation OR OLD.judge_reason IS NOT NEW.judge_reason\n'
  '  OR OLD.members_json IS NOT NEW.members_json OR OLD.linked_conflict IS NOT '
  'NEW.linked_conflict\n'
  '  OR OLD.derivation_json IS NOT NEW.derivation_json\n'
  'BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_conflict_delete',
  'conflict_case',
  'CREATE TRIGGER kb_conflict_delete AFTER DELETE ON conflict_case BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_conflict_insert',
  'conflict_case',
  'CREATE TRIGGER kb_conflict_insert AFTER INSERT ON conflict_case BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_conflict_update',
  'conflict_case',
  'CREATE TRIGGER kb_conflict_update AFTER UPDATE ON conflict_case\n'
  'WHEN OLD.conflict_id IS NOT NEW.conflict_id OR OLD.ctype IS NOT NEW.ctype\n'
  '  OR OLD.status IS NOT NEW.status OR OLD.side_a_json IS NOT NEW.side_a_json\n'
  '  OR OLD.side_b_json IS NOT NEW.side_b_json OR OLD.analysis_json IS NOT NEW.analysis_json\n'
  '  OR OLD.recommended_action IS NOT NEW.recommended_action\n'
  '  OR OLD.derivation_json IS NOT NEW.derivation_json\n'
  'BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_evidence_delete',
  'evidence_ref',
  'CREATE TRIGGER kb_evidence_delete AFTER DELETE ON evidence_ref BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_evidence_insert',
  'evidence_ref',
  'CREATE TRIGGER kb_evidence_insert AFTER INSERT ON evidence_ref BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_evidence_update',
  'evidence_ref',
  'CREATE TRIGGER kb_evidence_update AFTER UPDATE ON evidence_ref\n'
  'WHEN OLD.atom_id IS NOT NEW.atom_id OR OLD.video_id IS NOT NEW.video_id\n'
  '  OR OLD.modality IS NOT NEW.modality OR OLD.source_item_id IS NOT NEW.source_item_id\n'
  '  OR OLD.start_ms IS NOT NEW.start_ms OR OLD.end_ms IS NOT NEW.end_ms\n'
  '  OR OLD.evidence_text IS NOT NEW.evidence_text OR OLD.weight IS NOT NEW.weight\n'
  'BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'kb_source_update',
  'video_asset',
  'CREATE TRIGGER kb_source_update AFTER UPDATE ON video_asset\n'
  'WHEN OLD.source_published_at IS NOT NEW.source_published_at\n'
  '  OR OLD.source_url IS NOT NEW.source_url OR OLD.title IS NOT NEW.title\n'
  '  OR OLD.author IS NOT NEW.author\n'
  'BEGIN\n'
  "  UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='kb_revision';\n"
  'END'),
 ('trigger',
  'sync_source_admission_no_delete',
  'sync_source_admission',
  'CREATE TRIGGER sync_source_admission_no_delete BEFORE DELETE ON sync_source_admission BEGIN '
  "SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END"),
 ('trigger',
  'sync_source_admission_no_update',
  'sync_source_admission',
  'CREATE TRIGGER sync_source_admission_no_update BEFORE UPDATE ON sync_source_admission BEGIN '
  "SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END"),
 ('trigger',
  'sync_source_continuity_activation_no_delete',
  'sync_source_continuity_activation',
  'CREATE TRIGGER sync_source_continuity_activation_no_delete BEFORE DELETE ON '
  "sync_source_continuity_activation BEGIN SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END"),
 ('trigger',
  'sync_source_continuity_activation_no_update',
  'sync_source_continuity_activation',
  'CREATE TRIGGER sync_source_continuity_activation_no_update BEFORE UPDATE ON '
  "sync_source_continuity_activation BEGIN SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END"),
 ('trigger',
  'sync_source_control_disposition_no_delete',
  'sync_source_control_disposition',
  'CREATE TRIGGER sync_source_control_disposition_no_delete BEFORE DELETE ON '
  "sync_source_control_disposition BEGIN SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END"),
 ('trigger',
  'sync_source_control_disposition_no_update',
  'sync_source_control_disposition',
  'CREATE TRIGGER sync_source_control_disposition_no_update BEFORE UPDATE ON '
  "sync_source_control_disposition BEGIN SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END"),
 ('trigger',
  'sync_source_control_registration_no_delete',
  'sync_source_control_registration',
  'CREATE TRIGGER sync_source_control_registration_no_delete BEFORE DELETE ON '
  "sync_source_control_registration BEGIN SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END"),
 ('trigger',
  'sync_source_control_registration_no_update',
  'sync_source_control_registration',
  'CREATE TRIGGER sync_source_control_registration_no_update BEFORE UPDATE ON '
  "sync_source_control_registration BEGIN SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END"))

BASE_TABLE_CONTRACTS = (('atom_fts',
  ((0, 'atom_id', '', 0, None, 0, 0),
   (1, 'claim', '', 0, None, 0, 0),
   (2, 'reason', '', 0, None, 0, 0),
   (3, 'category', '', 0, None, 0, 0),
   (4, 'space', '', 0, None, 0, 0),
   (5, 'atom_fts', '', 0, None, 0, 1),
   (6, 'rank', '', 0, None, 0, 1)),
  (),
  ()),
 ('atom_fts_config',
  ((0, 'k', '', 1, None, 1, 0), (1, 'v', '', 0, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_atom_fts_config_1',
    1,
    'pk',
    0,
    ((0, 0, 'k', 0, 'BINARY', 1), (1, 1, 'v', 0, 'BINARY', 0))),)),
 ('atom_fts_content',
  ((0, 'id', 'INTEGER', 0, None, 1, 0),
   (1, 'c0', '', 0, None, 0, 0),
   (2, 'c1', '', 0, None, 0, 0),
   (3, 'c2', '', 0, None, 0, 0),
   (4, 'c3', '', 0, None, 0, 0),
   (5, 'c4', '', 0, None, 0, 0)),
  (),
  ()),
 ('atom_fts_data',
  ((0, 'id', 'INTEGER', 0, None, 1, 0), (1, 'block', 'BLOB', 0, None, 0, 0)),
  (),
  ()),
 ('atom_fts_docsize',
  ((0, 'id', 'INTEGER', 0, None, 1, 0), (1, 'sz', 'BLOB', 0, None, 0, 0)),
  (),
  ()),
 ('atom_fts_idx',
  ((0, 'segid', '', 1, None, 1, 0), (1, 'term', '', 1, None, 2, 0), (2, 'pgno', '', 0, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_atom_fts_idx_1',
    1,
    'pk',
    0,
    ((0, 0, 'segid', 0, 'BINARY', 1),
     (1, 1, 'term', 0, 'BINARY', 1),
     (2, 2, 'pgno', 0, 'BINARY', 0))),)),
 ('budget_attempt',
  ((0, 'attempt_id', 'TEXT', 1, None, 1, 0),
   (1, 'operation_id', 'TEXT', 1, None, 0, 0),
   (2, 'ordinal', 'INTEGER', 1, None, 0, 0),
   (3, 'request_sha256', 'TEXT', 1, None, 0, 0),
   (4, 'download_segment', 'TEXT', 0, None, 0, 0),
   (5, 'state', 'TEXT', 1, None, 0, 0),
   (6, 'initial_reservation_json', 'TEXT', 1, None, 0, 0),
   (7, 'initial_reservation_sha256', 'TEXT', 1, None, 0, 0),
   (8, 'current_reservation_json', 'TEXT', 1, None, 0, 0),
   (9, 'current_reservation_sha256', 'TEXT', 1, None, 0, 0),
   (10, 'reservation_revision', 'INTEGER', 1, '0', 0, 0),
   (11, 'reservation_held', 'INTEGER', 1, '1', 0, 0),
   (12, 'current_policy_sha256', 'TEXT', 1, None, 0, 0),
   (13, 'current_period_key', 'TEXT', 1, None, 0, 0),
   (14, 'current_window_start_utc', 'TEXT', 1, None, 0, 0),
   (15, 'current_window_end_utc', 'TEXT', 1, None, 0, 0),
   (16, 'first_dispatch_json', 'TEXT', 0, None, 0, 0),
   (17, 'first_dispatch_sha256', 'TEXT', 0, None, 0, 0),
   (18, 'dispatch_token_sha256', 'TEXT', 0, None, 0, 0),
   (19, 'dispatched_at', 'TEXT', 0, None, 0, 0),
   (20, 'last_admitted_at', 'TEXT', 1, None, 0, 0),
   (21, 'response_complete', 'INTEGER', 1, '0', 0, 0),
   (22, 'outcome', 'TEXT', 0, None, 0, 0),
   (23, 'observed_usage_json', 'TEXT', 0, None, 0, 0),
   (24, 'usage_quality', 'TEXT', 1, "'missing'", 0, 0),
   (25, 'provider_request_id', 'TEXT', 0, None, 0, 0),
   (26, 'result_text', 'TEXT', 0, None, 0, 0),
   (27, 'result_sha256', 'TEXT', 0, None, 0, 0),
   (28, 'result_bytes', 'INTEGER', 1, '0', 0, 0),
   (29, 'result_storage_state', 'TEXT', 1, "'none'", 0, 0),
   (30, 'settlement_sha256', 'TEXT', 0, None, 0, 0),
   (31, 'settled_at', 'TEXT', 0, None, 0, 0),
   (32, 'indeterminate_at', 'TEXT', 0, None, 0, 0),
   (33, 'created_at', 'TEXT', 1, None, 0, 0),
   (34, 'updated_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'budget_operation', 'operation_id', 'operation_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'idx_budget_attempt_operation',
    0,
    'c',
    0,
    ((0, 1, 'operation_id', 0, 'BINARY', 1),
     (1, 5, 'state', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_budget_attempt_window',
    0,
    'c',
    0,
    ((0, 5, 'state', 0, 'BINARY', 1),
     (1, 14, 'current_window_start_utc', 0, 'BINARY', 1),
     (2, 15, 'current_window_end_utc', 0, 'BINARY', 1),
     (3, -1, None, 0, 'BINARY', 0))),
   (2,
    'idx_budget_dispatch_token',
    1,
    'c',
    1,
    ((0, 18, 'dispatch_token_sha256', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (3,
    'idx_budget_attempt_ordinal',
    1,
    'c',
    0,
    ((0, 1, 'operation_id', 0, 'BINARY', 1),
     (1, 2, 'ordinal', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (4,
    'sqlite_autoindex_budget_attempt_1',
    1,
    'pk',
    0,
    ((0, 0, 'attempt_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('budget_operation',
  ((0, 'operation_id', 'TEXT', 1, None, 1, 0),
   (1, 'library_id', 'TEXT', 1, None, 0, 0),
   (2, 'identity_sha256', 'TEXT', 1, None, 0, 0),
   (3, 'continuation_id', 'TEXT', 1, None, 0, 0),
   (4, 'execution_generation', 'INTEGER', 1, None, 0, 0),
   (5, 'kind', 'TEXT', 1, None, 0, 0),
   (6, 'suboperation_key', 'TEXT', 1, None, 0, 0),
   (7, 'input_sha256', 'TEXT', 1, None, 0, 0),
   (8, 'root_job_id', 'INTEGER', 0, None, 0, 0),
   (9, 'max_attempts', 'INTEGER', 1, '8', 0, 0),
   (10, 'state', 'TEXT', 1, "'open'", 0, 0),
   (11, 'wait_reason', 'TEXT', 0, None, 0, 0),
   (12, 'resume_at', 'TEXT', 0, None, 0, 0),
   (13, 'wait_details_json', 'TEXT', 1, "'{}'", 0, 0),
   (14, 'revision', 'INTEGER', 1, '0', 0, 0),
   (15, 'created_at', 'TEXT', 1, None, 0, 0),
   (16, 'updated_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'job', 'root_job_id', 'job_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'idx_budget_operation_scope',
    1,
    'c',
    0,
    ((0, 1, 'library_id', 0, 'BINARY', 1),
     (1, 3, 'continuation_id', 0, 'BINARY', 1),
     (2, 4, 'execution_generation', 0, 'BINARY', 1),
     (3, 5, 'kind', 0, 'BINARY', 1),
     (4, 6, 'suboperation_key', 0, 'BINARY', 1),
     (5, 7, 'input_sha256', 0, 'BINARY', 1),
     (6, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_budget_operation_identity',
    1,
    'c',
    0,
    ((0, 2, 'identity_sha256', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (2,
    'sqlite_autoindex_budget_operation_1',
    1,
    'pk',
    0,
    ((0, 0, 'operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('checklist_check',
  ((0, 'check_id', 'TEXT', 0, None, 1, 0),
   (1, 'item_id', 'TEXT', 1, None, 0, 0),
   (2, 'item_revision', 'INTEGER', 1, None, 0, 0),
   (3, 'criteria_snapshot_hash', 'TEXT', 1, None, 0, 0),
   (4, 'operation_id', 'TEXT', 1, None, 0, 0),
   (5, 'commit_seq', 'INTEGER', 1, None, 0, 0),
   (6, 'record_kind', 'TEXT', 1, None, 0, 0),
   (7, 'result', 'TEXT', 0, None, 0, 0),
   (8, 'measurements_json', 'TEXT', 1, "'[]'", 0, 0),
   (9, 'note', 'TEXT', 1, "''", 0, 0),
   (10, 'attachment_refs_json', 'TEXT', 1, "'[]'", 0, 0),
   (11, 'checked_at', 'TEXT', 1, None, 0, 0),
   (12, 'created_at', 'TEXT', 1, None, 0, 0),
   (13, 'supersedes_check_id', 'TEXT', 0, None, 0, 0)),
  ((0, 0, 'checklist_check', 'supersedes_check_id', 'check_id', 'NO ACTION', 'NO ACTION', 'NONE'),
   (1, 0, 'checklist_item', 'item_id', 'item_id', 'NO ACTION', 'NO ACTION', 'NONE')),
  ((0,
    'idx_personal_check',
    0,
    'c',
    0,
    ((0, 1, 'item_id', 0, 'BINARY', 1),
     (1, 12, 'created_at', 0, 'BINARY', 1),
     (2, 0, 'check_id', 0, 'BINARY', 1),
     (3, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_checklist_check_3',
    1,
    'u',
    0,
    ((0, 1, 'item_id', 0, 'BINARY', 1),
     (1, 5, 'commit_seq', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (2,
    'sqlite_autoindex_checklist_check_2',
    1,
    'u',
    0,
    ((0, 4, 'operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (3,
    'sqlite_autoindex_checklist_check_1',
    1,
    'pk',
    0,
    ((0, 0, 'check_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('checklist_instance',
  ((0, 'instance_id', 'TEXT', 0, None, 1, 0),
   (1, 'project_id', 'TEXT', 1, None, 0, 0),
   (2, 'title', 'TEXT', 1, None, 0, 0),
   (3, 'space', 'TEXT', 1, "''", 0, 0),
   (4, 'stage', 'TEXT', 1, "''", 0, 0),
   (5, 'basis_kb_revision', 'INTEGER', 0, None, 0, 0),
   (6, 'lifecycle_state', 'TEXT', 1, "'active'", 0, 0),
   (7, 'revision', 'INTEGER', 1, '1', 0, 0),
   (8, 'created_at', 'TEXT', 1, None, 0, 0),
   (9, 'updated_at', 'TEXT', 1, None, 0, 0),
   (10, 'archived_at', 'TEXT', 0, None, 0, 0)),
  ((0, 0, 'renovation_project', 'project_id', 'project_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'sqlite_autoindex_checklist_instance_1',
    1,
    'pk',
    0,
    ((0, 0, 'instance_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('checklist_item',
  ((0, 'item_id', 'TEXT', 0, None, 1, 0),
   (1, 'instance_id', 'TEXT', 1, None, 0, 0),
   (2, 'title', 'TEXT', 1, None, 0, 0),
   (3, 'conditions_json', 'TEXT', 1, "'{}'", 0, 0),
   (4, 'method_text', 'TEXT', 1, "''", 0, 0),
   (5, 'basis_kind', 'TEXT', 1, None, 0, 0),
   (6, 'criteria_json', 'TEXT', 1, "'{}'", 0, 0),
   (7, 'criteria_state', 'TEXT', 1, "'needs_verification'", 0, 0),
   (8, 'position', 'INTEGER', 1, '0', 0, 0),
   (9, 'revision', 'INTEGER', 1, '1', 0, 0),
   (10, 'created_at', 'TEXT', 1, None, 0, 0),
   (11, 'updated_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'checklist_instance', 'instance_id', 'instance_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'idx_personal_item',
    0,
    'c',
    0,
    ((0, 1, 'instance_id', 0, 'BINARY', 1),
     (1, 8, 'position', 0, 'BINARY', 1),
     (2, 0, 'item_id', 0, 'BINARY', 1),
     (3, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_checklist_item_1',
    1,
    'pk',
    0,
    ((0, 0, 'item_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('conflict_case',
  ((0, 'conflict_id', 'TEXT', 0, None, 1, 0),
   (1, 'ctype', 'TEXT', 0, None, 0, 0),
   (2, 'status', 'TEXT', 0, None, 0, 0),
   (3, 'side_a_json', 'TEXT', 0, None, 0, 0),
   (4, 'side_b_json', 'TEXT', 0, None, 0, 0),
   (5, 'analysis_json', 'TEXT', 0, None, 0, 0),
   (6, 'recommended_action', 'TEXT', 0, None, 0, 0),
   (7, 'created_at', 'TEXT', 0, None, 0, 0),
   (8, 'derivation_json', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_conflict_case_1',
    1,
    'pk',
    0,
    ((0, 0, 'conflict_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('decision_card',
  ((0, 'card_id', 'TEXT', 0, None, 1, 0),
   (1, 'project_id', 'TEXT', 1, None, 0, 0),
   (2, 'title', 'TEXT', 1, None, 0, 0),
   (3, 'question', 'TEXT', 1, "''", 0, 0),
   (4, 'context_json', 'TEXT', 1, "'{}'", 0, 0),
   (5, 'space', 'TEXT', 1, "''", 0, 0),
   (6, 'stage', 'TEXT', 1, "''", 0, 0),
   (7, 'alternatives_json', 'TEXT', 1, "'[]'", 0, 0),
   (8, 'selected_option_id', 'TEXT', 0, None, 0, 0),
   (9, 'decision_text', 'TEXT', 1, "''", 0, 0),
   (10, 'decision_reason', 'TEXT', 1, "''", 0, 0),
   (11, 'open_questions_json', 'TEXT', 1, "'[]'", 0, 0),
   (12, 'content_snapshot_json', 'TEXT', 1, "'{}'", 0, 0),
   (13, 'state', 'TEXT', 1, "'considering'", 0, 0),
   (14, 'revision', 'INTEGER', 1, '1', 0, 0),
   (15, 'created_at', 'TEXT', 1, None, 0, 0),
   (16, 'updated_at', 'TEXT', 1, None, 0, 0),
   (17, 'archived_at', 'TEXT', 0, None, 0, 0)),
  ((0, 0, 'renovation_project', 'project_id', 'project_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'idx_personal_card',
    0,
    'c',
    0,
    ((0, 1, 'project_id', 0, 'BINARY', 1),
     (1, 13, 'state', 0, 'BINARY', 1),
     (2, 16, 'updated_at', 0, 'BINARY', 1),
     (3, 0, 'card_id', 0, 'BINARY', 1),
     (4, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_personal_card_space_stage',
    0,
    'c',
    0,
    ((0, 1, 'project_id', 0, 'BINARY', 1),
     (1, 5, 'space', 0, 'BINARY', 1),
     (2, 6, 'stage', 0, 'BINARY', 1),
     (3, 16, 'updated_at', 0, 'BINARY', 1),
     (4, 0, 'card_id', 0, 'BINARY', 1),
     (5, -1, None, 0, 'BINARY', 0))),
   (2,
    'sqlite_autoindex_decision_card_1',
    1,
    'pk',
    0,
    ((0, 0, 'card_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('evidence_ref',
  ((0, 'id', 'INTEGER', 0, None, 1, 0),
   (1, 'atom_id', 'TEXT', 0, None, 0, 0),
   (2, 'video_id', 'TEXT', 0, None, 0, 0),
   (3, 'modality', 'TEXT', 0, None, 0, 0),
   (4, 'source_item_id', 'TEXT', 0, None, 0, 0),
   (5, 'start_ms', 'INTEGER', 0, None, 0, 0),
   (6, 'end_ms', 'INTEGER', 0, None, 0, 0),
   (7, 'evidence_text', 'TEXT', 0, None, 0, 0),
   (8, 'weight', 'REAL', 0, None, 0, 0)),
  (),
  ((0,
    'idx_ev_atom',
    0,
    'c',
    0,
    ((0, 1, 'atom_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('frame',
  ((0, 'frame_id', 'TEXT', 1, None, 2, 0),
   (1, 'video_id', 'TEXT', 0, None, 1, 0),
   (2, 'pts_ms', 'INTEGER', 0, None, 0, 0),
   (3, 'origin', 'TEXT', 0, None, 0, 0),
   (4, 'dhash', 'TEXT', 0, None, 0, 0),
   (5, 'path', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_frame_1',
    1,
    'pk',
    0,
    ((0, 1, 'video_id', 0, 'BINARY', 1),
     (1, 0, 'frame_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),)),
 ('home_knowledge_recovery_operation',
  ((0, 'operation_id', 'TEXT', 1, None, 1, 0),
   (1, 'format_version', 'INTEGER', 1, None, 0, 0),
   (2, 'action', 'TEXT', 1, None, 0, 0),
   (3, 'library_id', 'TEXT', 1, None, 0, 0),
   (4, 'node_id', 'TEXT', 1, None, 0, 0),
   (5, 'source_kind', 'TEXT', 1, None, 0, 0),
   (6, 'source_id', 'TEXT', 1, None, 0, 0),
   (7, 'source_sha256', 'TEXT', 1, None, 0, 0),
   (8, 'source_binding_sha256', 'TEXT', 1, None, 0, 0),
   (9, 'before_library_epoch', 'INTEGER', 1, None, 0, 0),
   (10, 'before_kb_revision', 'INTEGER', 1, None, 0, 0),
   (11, 'after_library_epoch', 'INTEGER', 1, None, 0, 0),
   (12, 'after_kb_revision', 'INTEGER', 1, None, 0, 0),
   (13, 'before_version_json', 'TEXT', 1, None, 0, 0),
   (14, 'before_version_sha256', 'TEXT', 1, None, 0, 0),
   (15, 'after_version_json', 'TEXT', 1, None, 0, 0),
   (16, 'after_version_sha256', 'TEXT', 1, None, 0, 0),
   (17, 'protection_backup_id', 'TEXT', 1, None, 0, 0),
   (18, 'protection_descriptor_sha256', 'TEXT', 1, None, 0, 0),
   (19, 'protection_image_sha256', 'TEXT', 1, None, 0, 0),
   (20, 'original_core_sha256', 'TEXT', 0, None, 0, 0),
   (21, 'root_job_id', 'INTEGER', 0, None, 0, 0),
   (22, 'continuation_id', 'TEXT', 0, None, 0, 0),
   (23, 'execution_generation', 'INTEGER', 0, None, 0, 0),
   (24, 'frontier_json', 'TEXT', 1, None, 0, 0),
   (25, 'frontier_sha256', 'TEXT', 1, None, 0, 0),
   (26, 'before_operational_sha256', 'TEXT', 1, None, 0, 0),
   (27, 'after_operational_sha256', 'TEXT', 1, None, 0, 0),
   (28, 'checkpoint_json', 'TEXT', 1, None, 0, 0),
   (29, 'checkpoint_sha256', 'TEXT', 1, None, 0, 0),
   (30, 'request_json', 'TEXT', 1, None, 0, 0),
   (31, 'request_sha256', 'TEXT', 1, None, 0, 0),
   (32, 'result_json', 'TEXT', 1, None, 0, 0),
   (33, 'result_sha256', 'TEXT', 1, None, 0, 0),
   (34, 'committed_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'job', 'root_job_id', 'job_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'idx_home_recovery_root',
    0,
    'c',
    1,
    ((0, 21, 'root_job_id', 0, 'BINARY', 1),
     (1, 23, 'execution_generation', 0, 'BINARY', 1),
     (2, 34, 'committed_at', 0, 'BINARY', 1),
     (3, 0, 'operation_id', 0, 'BINARY', 1),
     (4, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_home_recovery_epoch',
    1,
    'c',
    0,
    ((0, 3, 'library_id', 0, 'BINARY', 1),
     (1, 11, 'after_library_epoch', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (2,
    'idx_home_recovery_committed',
    0,
    'c',
    0,
    ((0, 34, 'committed_at', 0, 'BINARY', 1),
     (1, 0, 'operation_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (3,
    'sqlite_autoindex_home_knowledge_recovery_operation_1',
    1,
    'pk',
    0,
    ((0, 0, 'operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('job',
  ((0, 'job_id', 'INTEGER', 0, None, 1, 0),
   (1, 'video_id', 'TEXT', 0, None, 0, 0),
   (2, 'kind', 'TEXT', 0, None, 0, 0),
   (3, 'status', 'TEXT', 0, "'pending'", 0, 0),
   (4, 'attempts', 'INTEGER', 0, '0', 0, 0),
   (5, 'error', 'TEXT', 0, None, 0, 0),
   (6, 'created_at', 'TEXT', 0, None, 0, 0),
   (7, 'updated_at', 'TEXT', 0, None, 0, 0),
   (8, 'request_id', 'TEXT', 0, None, 0, 0),
   (9, 'scope_key', 'TEXT', 0, None, 0, 0),
   (10, 'payload_json', 'TEXT', 1, "'{}'", 0, 0),
   (11, 'owner_id', 'TEXT', 0, None, 0, 0),
   (12, 'lease_until', 'INTEGER', 0, None, 0, 0),
   (13, 'result_json', 'TEXT', 0, None, 0, 0),
   (14, 'started_at', 'TEXT', 0, None, 0, 0),
   (15, 'completed_at', 'TEXT', 0, None, 0, 0),
   (16, 'continuation_id', 'TEXT', 0, None, 0, 0),
   (17, 'execution_generation', 'INTEGER', 1, '1', 0, 0),
   (18, 'not_before', 'INTEGER', 0, None, 0, 0),
   (19, 'blocked_reason', 'TEXT', 0, None, 0, 0),
   (20, 'wait_mode', 'TEXT', 1, "'none'", 0, 0),
   (21, 'manual_paused', 'INTEGER', 1, '0', 0, 0),
   (22, 'budget_operation_id', 'TEXT', 0, None, 0, 0)),
  ((0,
    0,
    'budget_operation',
    'budget_operation_id',
    'operation_id',
    'NO ACTION',
    'NO ACTION',
    'NONE'),),
  ((0,
    'idx_budget_job_lineage',
    0,
    'c',
    0,
    ((0, 16, 'continuation_id', 0, 'BINARY', 1),
     (1, 17, 'execution_generation', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_job_active_scope',
    1,
    'c',
    1,
    ((0, 9, 'scope_key', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (2,
    'idx_job_request',
    1,
    'c',
    0,
    ((0, 8, 'request_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (3,
    'idx_job_status',
    0,
    'c',
    0,
    ((0, 3, 'status', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('knowledge_atom',
  ((0, 'id', 'TEXT', 0, None, 1, 0),
   (1, 'video_id', 'TEXT', 0, None, 0, 0),
   (2, 'category', 'TEXT', 0, None, 0, 0),
   (3, 'stage', 'TEXT', 0, None, 0, 0),
   (4, 'space', 'TEXT', 0, None, 0, 0),
   (5, 'subject', 'TEXT', 0, None, 0, 0),
   (6, 'claim', 'TEXT', 0, None, 0, 0),
   (7, 'reason', 'TEXT', 0, None, 0, 0),
   (8, 'risk_if_ignored', 'TEXT', 0, None, 0, 0),
   (9, 'polarity', 'TEXT', 0, None, 0, 0),
   (10, 'conditions_json', 'TEXT', 0, None, 0, 0),
   (11, 'parameters_json', 'TEXT', 0, None, 0, 0),
   (12, 'confidence', 'REAL', 0, None, 0, 0),
   (13, 'status', 'TEXT', 0, "'candidate'", 0, 0),
   (14, 'cluster_id', 'TEXT', 0, None, 0, 0),
   (15, 'model', 'TEXT', 0, None, 0, 0),
   (16, 'prompt_version', 'TEXT', 0, None, 0, 0),
   (17, 'created_at', 'TEXT', 0, None, 0, 0),
   (18, 'dimension', 'TEXT', 0, None, 0, 0),
   (19, 'evidence_nature', 'TEXT', 0, None, 0, 0),
   (20, 'exceptions', 'TEXT', 0, None, 0, 0),
   (21, 'prices_json', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'idx_atom_video',
    0,
    'c',
    0,
    ((0, 1, 'video_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_knowledge_atom_1',
    1,
    'pk',
    0,
    ((0, 0, 'id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('knowledge_cluster',
  ((0, 'cluster_id', 'TEXT', 0, None, 1, 0),
   (1, 'canonical_topic', 'TEXT', 0, None, 0, 0),
   (2, 'relation', 'TEXT', 0, None, 0, 0),
   (3, 'judge_reason', 'TEXT', 0, None, 0, 0),
   (4, 'members_json', 'TEXT', 0, None, 0, 0),
   (5, 'linked_conflict', 'TEXT', 0, None, 0, 0),
   (6, 'created_at', 'TEXT', 0, None, 0, 0),
   (7, 'derivation_json', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_knowledge_cluster_1',
    1,
    'pk',
    0,
    ((0, 0, 'cluster_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('meta',
  ((0, 'key', 'TEXT', 0, None, 1, 0), (1, 'value', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_meta_1',
    1,
    'pk',
    0,
    ((0, 0, 'key', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('mutation_receipt',
  ((0, 'operation_id', 'TEXT', 0, None, 1, 0),
   (1, 'scope', 'TEXT', 1, None, 0, 0),
   (2, 'request_hash', 'TEXT', 1, None, 0, 0),
   (3, 'response_status', 'INTEGER', 1, None, 0, 0),
   (4, 'response_json', 'TEXT', 1, None, 0, 0),
   (5, 'created_at', 'TEXT', 1, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_mutation_receipt_1',
    1,
    'pk',
    0,
    ((0, 0, 'operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('ocr_span',
  ((0, 'id', 'TEXT', 1, None, 2, 0),
   (1, 'video_id', 'TEXT', 0, None, 1, 0),
   (2, 'start_ms', 'INTEGER', 0, None, 0, 0),
   (3, 'end_ms', 'INTEGER', 0, None, 0, 0),
   (4, 'text', 'TEXT', 0, None, 0, 0),
   (5, 'conf', 'REAL', 0, None, 0, 0),
   (6, 'frame_ids_json', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'idx_ocr_video',
    0,
    'c',
    0,
    ((0, 1, 'video_id', 0, 'BINARY', 1),
     (1, 2, 'start_ms', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_ocr_span_1',
    1,
    'pk',
    0,
    ((0, 1, 'video_id', 0, 'BINARY', 1),
     (1, 0, 'id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))))),
 ('personal_evidence_ref',
  ((0, 'ref_id', 'TEXT', 0, None, 1, 0),
   (1, 'project_id', 'TEXT', 1, None, 0, 0),
   (2, 'owner_kind', 'TEXT', 1, None, 0, 0),
   (3, 'owner_id', 'TEXT', 1, None, 0, 0),
   (4, 'target_kind', 'TEXT', 1, None, 0, 0),
   (5, 'target_id', 'TEXT', 1, None, 0, 0),
   (6, 'fingerprint_version', 'INTEGER', 1, '1', 0, 0),
   (7, 'target_fingerprint', 'TEXT', 0, None, 0, 0),
   (8, 'snapshot_json', 'TEXT', 1, None, 0, 0),
   (9, 'created_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'renovation_project', 'project_id', 'project_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'idx_personal_ref_owner',
    0,
    'c',
    0,
    ((0, 2, 'owner_kind', 0, 'BINARY', 1),
     (1, 3, 'owner_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_personal_ref_target',
    0,
    'c',
    0,
    ((0, 4, 'target_kind', 0, 'BINARY', 1),
     (1, 5, 'target_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (2,
    'sqlite_autoindex_personal_evidence_ref_1',
    1,
    'pk',
    0,
    ((0, 0, 'ref_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('personal_revision',
  ((0, 'entity_kind', 'TEXT', 1, None, 1, 0),
   (1, 'entity_id', 'TEXT', 1, None, 2, 0),
   (2, 'revision', 'INTEGER', 1, None, 3, 0),
   (3, 'snapshot_json', 'TEXT', 1, None, 0, 0),
   (4, 'operation_id', 'TEXT', 0, None, 0, 0),
   (5, 'created_at', 'TEXT', 1, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_personal_revision_1',
    1,
    'pk',
    0,
    ((0, 0, 'entity_kind', 0, 'BINARY', 1),
     (1, 1, 'entity_id', 0, 'BINARY', 1),
     (2, 2, 'revision', 0, 'BINARY', 1),
     (3, -1, None, 0, 'BINARY', 0))),)),
 ('processing_run',
  ((0, 'run_id', 'INTEGER', 0, None, 1, 0),
   (1, 'video_id', 'TEXT', 0, None, 0, 0),
   (2, 'step', 'TEXT', 0, None, 0, 0),
   (3, 'model', 'TEXT', 0, None, 0, 0),
   (4, 'prompt_version', 'TEXT', 0, None, 0, 0),
   (5, 'schema_version', 'TEXT', 0, None, 0, 0),
   (6, 'taxonomy_version', 'TEXT', 0, None, 0, 0),
   (7, 'started_at', 'TEXT', 0, None, 0, 0),
   (8, 'completed_at', 'TEXT', 0, None, 0, 0),
   (9, 'ok', 'INTEGER', 0, None, 0, 0),
   (10, 'detail', 'TEXT', 0, None, 0, 0)),
  (),
  ()),
 ('renovation_project',
  ((0, 'project_id', 'TEXT', 0, None, 1, 0),
   (1, 'name', 'TEXT', 1, None, 0, 0),
   (2, 'profile_json', 'TEXT', 1, "'{}'", 0, 0),
   (3, 'revision', 'INTEGER', 1, '1', 0, 0),
   (4, 'created_at', 'TEXT', 1, None, 0, 0),
   (5, 'updated_at', 'TEXT', 1, None, 0, 0),
   (6, 'archived_at', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_renovation_project_1',
    1,
    'pk',
    0,
    ((0, 0, 'project_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('source_attachment',
  ((0, 'attachment_id', 'TEXT', 0, None, 1, 0),
   (1, 'source_id', 'TEXT', 1, None, 0, 0),
   (2, 'project_id', 'TEXT', 1, None, 0, 0),
   (3, 'sha256', 'TEXT', 0, None, 0, 0),
   (4, 'byte_size', 'INTEGER', 0, None, 0, 0),
   (5, 'media_type', 'TEXT', 1, None, 0, 0),
   (6, 'original_name', 'TEXT', 1, None, 0, 0),
   (7, 'storage_key', 'TEXT', 1, None, 0, 0),
   (8, 'state', 'TEXT', 1, None, 0, 0),
   (9, 'created_at', 'TEXT', 1, None, 0, 0),
   (10, 'operation_id', 'TEXT', 1, None, 0, 0),
   (11, 'error', 'TEXT', 0, None, 0, 0)),
  ((0, 0, 'renovation_project', 'project_id', 'project_id', 'NO ACTION', 'NO ACTION', 'NONE'),
   (1, 0, 'source_record', 'source_id', 'source_id', 'NO ACTION', 'NO ACTION', 'NONE')),
  ((0,
    'idx_personal_attachment_source',
    0,
    'c',
    0,
    ((0, 1, 'source_id', 0, 'BINARY', 1),
     (1, 8, 'state', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_source_attachment_3',
    1,
    'u',
    0,
    ((0, 10, 'operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (2,
    'sqlite_autoindex_source_attachment_2',
    1,
    'u',
    0,
    ((0, 7, 'storage_key', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (3,
    'sqlite_autoindex_source_attachment_1',
    1,
    'pk',
    0,
    ((0, 0, 'attachment_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('source_record',
  ((0, 'source_id', 'TEXT', 0, None, 1, 0),
   (1, 'project_id', 'TEXT', 1, None, 0, 0),
   (2, 'source_type', 'TEXT', 1, None, 0, 0),
   (3, 'title', 'TEXT', 1, None, 0, 0),
   (4, 'publisher', 'TEXT', 1, "''", 0, 0),
   (5, 'document_no', 'TEXT', 1, "''", 0, 0),
   (6, 'edition', 'TEXT', 1, "''", 0, 0),
   (7, 'published_on', 'TEXT', 1, "''", 0, 0),
   (8, 'valid_from', 'TEXT', 1, "''", 0, 0),
   (9, 'valid_to', 'TEXT', 1, "''", 0, 0),
   (10, 'source_url', 'TEXT', 1, "''", 0, 0),
   (11, 'locator', 'TEXT', 1, "''", 0, 0),
   (12, 'excerpt', 'TEXT', 1, "''", 0, 0),
   (13, 'scope_json', 'TEXT', 1, "'{}'", 0, 0),
   (14, 'verification_state', 'TEXT', 1, "'unverified'", 0, 0),
   (15, 'validity', 'TEXT', 1, "'unknown'", 0, 0),
   (16, 'checked_at', 'TEXT', 0, None, 0, 0),
   (17, 'check_note', 'TEXT', 1, "''", 0, 0),
   (18, 'revision', 'INTEGER', 1, '1', 0, 0),
   (19, 'created_at', 'TEXT', 1, None, 0, 0),
   (20, 'updated_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'renovation_project', 'project_id', 'project_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'idx_personal_source',
    0,
    'c',
    0,
    ((0, 1, 'project_id', 0, 'BINARY', 1),
     (1, 20, 'updated_at', 0, 'BINARY', 1),
     (2, 0, 'source_id', 0, 'BINARY', 1),
     (3, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_source_record_1',
    1,
    'pk',
    0,
    ((0, 0, 'source_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sqlite_sequence', ((0, 'name', '', 0, None, 0, 0), (1, 'seq', '', 0, None, 0, 0)), (), ()),
 ('storage_artifact',
  ((0, 'artifact_id', 'TEXT', 1, None, 1, 0),
   (1, 'namespace', 'TEXT', 1, None, 0, 0),
   (2, 'root_id', 'TEXT', 1, None, 0, 0),
   (3, 'root_device', 'TEXT', 1, None, 0, 0),
   (4, 'root_inode', 'TEXT', 1, None, 0, 0),
   (5, 'storage_key', 'TEXT', 1, None, 0, 0),
   (6, 'sha256', 'TEXT', 1, None, 0, 0),
   (7, 'byte_size', 'INTEGER', 1, None, 0, 0),
   (8, 'file_device', 'TEXT', 1, None, 0, 0),
   (9, 'file_inode', 'TEXT', 1, None, 0, 0),
   (10, 'file_mtime_ns', 'TEXT', 1, None, 0, 0),
   (11, 'file_ctime_ns', 'TEXT', 1, None, 0, 0),
   (12, 'created_at', 'TEXT', 1, None, 0, 0),
   (13, 'updated_at', 'TEXT', 1, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_storage_artifact_2',
    1,
    'u',
    0,
    ((0, 1, 'namespace', 0, 'BINARY', 1),
     (1, 2, 'root_id', 0, 'BINARY', 1),
     (2, 5, 'storage_key', 0, 'BINARY', 1),
     (3, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_storage_artifact_1',
    1,
    'pk',
    0,
    ((0, 0, 'artifact_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('storage_gc_item',
  ((0, 'operation_id', 'TEXT', 1, None, 1, 0),
   (1, 'artifact_id', 'TEXT', 1, None, 2, 0),
   (2, 'namespace', 'TEXT', 1, None, 0, 0),
   (3, 'root_id', 'TEXT', 1, None, 0, 0),
   (4, 'storage_key', 'TEXT', 1, None, 0, 0),
   (5, 'sha256', 'TEXT', 1, None, 0, 0),
   (6, 'byte_size', 'INTEGER', 1, None, 0, 0),
   (7, 'file_device', 'TEXT', 1, None, 0, 0),
   (8, 'file_inode', 'TEXT', 1, None, 0, 0),
   (9, 'file_mtime_ns', 'TEXT', 1, None, 0, 0),
   (10, 'file_ctime_ns', 'TEXT', 1, None, 0, 0),
   (11, 'state', 'TEXT', 1, None, 0, 0),
   (12, 'deleted_bytes', 'INTEGER', 1, '0', 0, 0),
   (13, 'reason_code', 'TEXT', 0, None, 0, 0),
   (14, 'updated_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'storage_artifact', 'artifact_id', 'artifact_id', 'NO ACTION', 'NO ACTION', 'NONE'),
   (1,
    0,
    'storage_gc_operation',
    'operation_id',
    'operation_id',
    'NO ACTION',
    'NO ACTION',
    'NONE')),
  ((0,
    'idx_gc_item_state',
    0,
    'c',
    0,
    ((0, 0, 'operation_id', 0, 'BINARY', 1),
     (1, 11, 'state', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_storage_gc_item_1',
    1,
    'pk',
    0,
    ((0, 0, 'operation_id', 0, 'BINARY', 1),
     (1, 1, 'artifact_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))))),
 ('storage_gc_operation',
  ((0, 'operation_id', 'TEXT', 1, None, 1, 0),
   (1, 'request_sha256', 'TEXT', 1, None, 0, 0),
   (2, 'preview_sha256', 'TEXT', 1, None, 0, 0),
   (3, 'roots_sha256', 'TEXT', 1, None, 0, 0),
   (4, 'policy_json', 'TEXT', 1, None, 0, 0),
   (5, 'request_json', 'TEXT', 1, None, 0, 0),
   (6, 'result_json', 'TEXT', 0, None, 0, 0),
   (7, 'state', 'TEXT', 1, None, 0, 0),
   (8, 'created_at', 'TEXT', 1, None, 0, 0),
   (9, 'updated_at', 'TEXT', 1, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_storage_gc_operation_1',
    1,
    'pk',
    0,
    ((0, 0, 'operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('sync_checkpoint',
  ((0, 'checkpoint_id', 'TEXT', 0, None, 1, 0),
   (1, 'publication_id', 'TEXT', 1, None, 0, 0),
   (2, 'receiver_version_json', 'TEXT', 1, None, 0, 0),
   (3, 'format_version', 'INTEGER', 1, None, 0, 0),
   (4, 'schema_version', 'INTEGER', 1, None, 0, 0),
   (5, 'storage_key', 'TEXT', 1, None, 0, 0),
   (6, 'sha256', 'TEXT', 1, None, 0, 0),
   (7, 'byte_size', 'INTEGER', 1, None, 0, 0),
   (8, 'created_at', 'TEXT', 1, None, 0, 0)),
  ((0,
    0,
    'sync_publication',
    'publication_id',
    'publication_id',
    'NO ACTION',
    'NO ACTION',
    'NONE'),),
  ((0,
    'idx_sync_checkpoint_created',
    0,
    'c',
    0,
    ((0, 8, 'created_at', 0, 'BINARY', 1),
     (1, 0, 'checkpoint_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_sync_checkpoint_3',
    1,
    'u',
    0,
    ((0, 5, 'storage_key', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (2,
    'sqlite_autoindex_sync_checkpoint_2',
    1,
    'u',
    0,
    ((0, 1, 'publication_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (3,
    'sqlite_autoindex_sync_checkpoint_1',
    1,
    'pk',
    0,
    ((0, 0, 'checkpoint_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_checkpoint_retirement',
  ((0, 'origin_kind', 'TEXT', 1, None, 1, 0),
   (1, 'checkpoint_id', 'TEXT', 1, None, 2, 0),
   (2, 'operation_id', 'TEXT', 1, None, 0, 0),
   (3, 'storage_key', 'TEXT', 1, None, 0, 0),
   (4, 'sha256', 'TEXT', 1, None, 0, 0),
   (5, 'byte_size', 'INTEGER', 1, None, 0, 0),
   (6, 'retired_at', 'TEXT', 1, None, 0, 0)),
  ((0,
    0,
    'storage_gc_operation',
    'operation_id',
    'operation_id',
    'NO ACTION',
    'NO ACTION',
    'NONE'),),
  ((0,
    'sqlite_autoindex_sync_checkpoint_retirement_1',
    1,
    'pk',
    0,
    ((0, 0, 'origin_kind', 0, 'BINARY', 1),
     (1, 1, 'checkpoint_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),)),
 ('sync_pair',
  ((0, 'slot', 'INTEGER', 0, None, 1, 0),
   (1, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (2, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (3, 'target_library_id', 'TEXT', 1, None, 0, 0),
   (4, 'target_node_id', 'TEXT', 1, None, 0, 0),
   (5, 'protocol_version', 'INTEGER', 1, None, 0, 0),
   (6, 'schema_fingerprint', 'TEXT', 1, None, 0, 0),
   (7, 'paired_at', 'TEXT', 1, None, 0, 0)),
  (),
  ()),
 ('sync_publication',
  ((0, 'publication_id', 'TEXT', 0, None, 1, 0),
   (1, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (2, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (3, 'source_epoch', 'INTEGER', 1, None, 0, 0),
   (4, 'source_revision', 'INTEGER', 1, None, 0, 0),
   (5, 'payload_sha256', 'TEXT', 1, None, 0, 0),
   (6, 'knowledge_sha256', 'TEXT', 1, None, 0, 0),
   (7, 'committed_at', 'TEXT', 1, None, 0, 0),
   (8, 'local_version_json', 'TEXT', 1, None, 0, 0)),
  (),
  ((0,
    'idx_sync_publication_source',
    0,
    'c',
    0,
    ((0, 1, 'source_library_id', 0, 'BINARY', 1),
     (1, 2, 'source_node_id', 0, 'BINARY', 1),
     (2, 3, 'source_epoch', 0, 'BINARY', 1),
     (3, 4, 'source_revision', 0, 'BINARY', 1),
     (4, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_sync_publication_1',
    1,
    'pk',
    0,
    ((0, 0, 'publication_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_publication_fence',
  ((0, 'publication_id', 'TEXT', 0, None, 1, 0), (1, 'generation', 'INTEGER', 1, None, 0, 0)),
  ((0,
    0,
    'sync_publication',
    'publication_id',
    'publication_id',
    'NO ACTION',
    'NO ACTION',
    'NONE'),),
  ((0,
    'sqlite_autoindex_sync_publication_fence_1',
    1,
    'pk',
    0,
    ((0, 0, 'publication_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('sync_published_video',
  ((0, 'publication_id', 'TEXT', 1, None, 1, 0),
   (1, 'video_id', 'TEXT', 1, None, 2, 0),
   (2, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (3, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (4, 'source_epoch', 'INTEGER', 1, None, 0, 0),
   (5, 'source_revision', 'INTEGER', 1, None, 0, 0),
   (6, 'media_sha256', 'TEXT', 1, None, 0, 0),
   (7, 'committed_at', 'TEXT', 1, None, 0, 0)),
  (),
  ((0,
    'idx_sync_publication_video',
    0,
    'c',
    0,
    ((0, 1, 'video_id', 0, 'BINARY', 1),
     (1, 7, 'committed_at', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_sync_published_video_1',
    1,
    'pk',
    0,
    ((0, 0, 'publication_id', 0, 'BINARY', 1),
     (1, 1, 'video_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))))),
 ('sync_queue',
  ((0, 'item_id', 'INTEGER', 0, None, 1, 0),
   (1, 'url', 'TEXT', 1, None, 0, 0),
   (2, 'status', 'TEXT', 0, "'queued'", 0, 0),
   (3, 'attempts', 'INTEGER', 0, '0', 0, 0),
   (4, 'lease_until', 'TEXT', 0, None, 0, 0),
   (5, 'last_error_class', 'TEXT', 0, None, 0, 0),
   (6, 'created_at', 'TEXT', 0, None, 0, 0),
   (7, 'updated_at', 'TEXT', 0, None, 0, 0),
   (8, 'owner_id', 'TEXT', 0, None, 0, 0),
   (9, 'owner_generation', 'INTEGER', 1, '0', 0, 0),
   (10, 'lease_token', 'TEXT', 0, None, 0, 0),
   (11, 'lease_generation', 'INTEGER', 1, '0', 0, 0),
   (12, 'retry_at', 'TEXT', 0, None, 0, 0),
   (13, 'video_id', 'TEXT', 0, None, 0, 0),
   (14, 'publication_id', 'TEXT', 0, None, 0, 0),
   (15, 'result_hash', 'TEXT', 0, None, 0, 0),
   (16, 'budget_wait_reason', 'TEXT', 0, None, 0, 0),
   (17, 'budget_operation_id', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'idx_syncq_lease',
    0,
    'c',
    0,
    ((0, 2, 'status', 0, 'BINARY', 1),
     (1, 4, 'lease_until', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_syncq_owner',
    0,
    'c',
    0,
    ((0, 8, 'owner_id', 0, 'BINARY', 1),
     (1, 9, 'owner_generation', 0, 'BINARY', 1),
     (2, 2, 'status', 0, 'BINARY', 1),
     (3, -1, None, 0, 'BINARY', 0))),
   (2,
    'idx_syncq_claim',
    0,
    'c',
    0,
    ((0, 2, 'status', 0, 'BINARY', 1),
     (1, 12, 'retry_at', 0, 'BINARY', 1),
     (2, 0, 'item_id', 0, 'BINARY', 1),
     (3, -1, None, 0, 'BINARY', 0))),
   (3,
    'idx_syncq_status',
    0,
    'c',
    0,
    ((0, 2, 'status', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_queue_budget_receipt',
  ((0, 'request_id', 'TEXT', 0, None, 1, 0),
   (1, 'request_sha256', 'TEXT', 1, None, 0, 0),
   (2, 'request_json', 'TEXT', 1, None, 0, 0),
   (3, 'action', 'TEXT', 1, None, 0, 0),
   (4, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (5, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (6, 'target_library_id', 'TEXT', 1, None, 0, 0),
   (7, 'target_node_id', 'TEXT', 1, None, 0, 0),
   (8, 'receiver_library_epoch', 'INTEGER', 1, None, 0, 0),
   (9, 'recovery_generation', 'INTEGER', 1, None, 0, 0),
   (10, 'item_id', 'INTEGER', 1, None, 0, 0),
   (11, 'lease_generation', 'INTEGER', 1, None, 0, 0),
   (12, 'result_json', 'TEXT', 1, None, 0, 0),
   (13, 'result_sha256', 'TEXT', 1, None, 0, 0),
   (14, 'committed_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'sync_queue', 'item_id', 'item_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'idx_sync_budget_claim_defer',
    1,
    'c',
    1,
    ((0, 10, 'item_id', 0, 'BINARY', 1),
     (1, 11, 'lease_generation', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_sync_queue_budget_receipt_1',
    1,
    'pk',
    0,
    ((0, 0, 'request_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_queue_publication',
  ((0, 'item_id', 'INTEGER', 1, None, 1, 0),
   (1, 'lease_generation', 'INTEGER', 1, None, 2, 0),
   (2, 'lease_token', 'TEXT', 1, None, 0, 0),
   (3, 'owner_id', 'TEXT', 1, None, 0, 0),
   (4, 'owner_generation', 'INTEGER', 1, None, 0, 0),
   (5, 'publication_id', 'TEXT', 1, None, 0, 0),
   (6, 'video_id', 'TEXT', 1, None, 0, 0),
   (7, 'created_at', 'TEXT', 1, None, 0, 0)),
  ((0,
    0,
    'sync_published_video',
    'publication_id',
    'publication_id',
    'NO ACTION',
    'NO ACTION',
    'NONE'),
   (0, 1, 'sync_published_video', 'video_id', 'video_id', 'NO ACTION', 'NO ACTION', 'NONE'),
   (1, 0, 'sync_queue', 'item_id', 'item_id', 'NO ACTION', 'NO ACTION', 'NONE')),
  ((0,
    'sqlite_autoindex_sync_queue_publication_1',
    1,
    'pk',
    0,
    ((0, 0, 'item_id', 0, 'BINARY', 1),
     (1, 1, 'lease_generation', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),)),
 ('sync_queue_source_receipt',
  ((0, 'request_id', 'TEXT', 0, None, 1, 0),
   (1, 'request_sha256', 'TEXT', 1, None, 0, 0),
   (2, 'request_json', 'TEXT', 1, None, 0, 0),
   (3, 'action', 'TEXT', 1, None, 0, 0),
   (4, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (5, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (6, 'target_library_id', 'TEXT', 1, None, 0, 0),
   (7, 'target_node_id', 'TEXT', 1, None, 0, 0),
   (8, 'receiver_library_epoch', 'INTEGER', 1, None, 0, 0),
   (9, 'recovery_generation', 'INTEGER', 1, None, 0, 0),
   (10, 'item_id', 'INTEGER', 1, None, 0, 0),
   (11, 'lease_generation', 'INTEGER', 1, None, 0, 0),
   (12, 'core_sha256', 'TEXT', 1, None, 0, 0),
   (13, 'submitted_url_sha256', 'TEXT', 1, None, 0, 0),
   (14, 'failure_sha256', 'TEXT', 1, None, 0, 0),
   (15, 'result_json', 'TEXT', 1, None, 0, 0),
   (16, 'result_sha256', 'TEXT', 1, None, 0, 0),
   (17, 'committed_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'sync_queue', 'item_id', 'item_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'idx_sync_source_terminal_item',
    1,
    'c',
    0,
    ((0, 10, 'item_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_sync_queue_source_receipt_1',
    1,
    'pk',
    0,
    ((0, 0, 'request_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_recovery_checkpoint',
  ((0, 'checkpoint_id', 'TEXT', 0, None, 1, 0),
   (1, 'operation_id', 'TEXT', 1, None, 0, 0),
   (2, 'origin_kind', 'TEXT', 1, None, 0, 0),
   (3, 'receiver_version_json', 'TEXT', 1, None, 0, 0),
   (4, 'format_version', 'INTEGER', 1, None, 0, 0),
   (5, 'schema_version', 'INTEGER', 1, None, 0, 0),
   (6, 'storage_key', 'TEXT', 1, None, 0, 0),
   (7, 'sha256', 'TEXT', 1, None, 0, 0),
   (8, 'byte_size', 'INTEGER', 1, None, 0, 0),
   (9, 'created_at', 'TEXT', 1, None, 0, 0)),
  ((0,
    0,
    'sync_recovery_operation',
    'operation_id',
    'operation_id',
    'NO ACTION',
    'NO ACTION',
    'NONE'),),
  ((0,
    'idx_sync_recovery_checkpoint_created',
    0,
    'c',
    0,
    ((0, 9, 'created_at', 0, 'BINARY', 1),
     (1, 0, 'checkpoint_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_sync_recovery_checkpoint_3',
    1,
    'u',
    0,
    ((0, 6, 'storage_key', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (2,
    'sqlite_autoindex_sync_recovery_checkpoint_2',
    1,
    'u',
    0,
    ((0, 1, 'operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (3,
    'sqlite_autoindex_sync_recovery_checkpoint_1',
    1,
    'pk',
    0,
    ((0, 0, 'checkpoint_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_recovery_control',
  ((0, 'slot', 'INTEGER', 0, None, 1, 0),
   (1, 'state', 'TEXT', 1, None, 0, 0),
   (2, 'generation', 'INTEGER', 1, None, 0, 0),
   (3, 'revision', 'INTEGER', 1, None, 0, 0),
   (4, 'source_floor_json', 'TEXT', 0, None, 0, 0),
   (5, 'restore_operation_id', 'TEXT', 0, None, 0, 0),
   (6, 'updated_at', 'TEXT', 1, None, 0, 0)),
  ((0,
    0,
    'sync_recovery_operation',
    'restore_operation_id',
    'operation_id',
    'NO ACTION',
    'NO ACTION',
    'NONE'),),
  ()),
 ('sync_recovery_operation',
  ((0, 'operation_id', 'TEXT', 0, None, 1, 0),
   (1, 'action', 'TEXT', 1, None, 0, 0),
   (2, 'request_sha256', 'TEXT', 1, None, 0, 0),
   (3, 'request_json', 'TEXT', 1, None, 0, 0),
   (4, 'result_json', 'TEXT', 1, None, 0, 0),
   (5, 'created_at', 'TEXT', 1, None, 0, 0)),
  (),
  ((0,
    'idx_sync_recovery_created',
    0,
    'c',
    0,
    ((0, 5, 'created_at', 0, 'BINARY', 1),
     (1, 0, 'operation_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_sync_recovery_operation_1',
    1,
    'pk',
    0,
    ((0, 0, 'operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_report',
  ((0, 'name', 'TEXT', 0, None, 1, 0),
   (1, 'publication_id', 'TEXT', 1, None, 0, 0),
   (2, 'storage_key', 'TEXT', 1, None, 0, 0),
   (3, 'sha256', 'TEXT', 1, None, 0, 0),
   (4, 'byte_size', 'INTEGER', 1, None, 0, 0),
   (5, 'policy_version', 'TEXT', 1, None, 0, 0),
   (6, 'source_version_json', 'TEXT', 1, None, 0, 0),
   (7, 'local_version_json', 'TEXT', 1, None, 0, 0),
   (8, 'generated_at', 'TEXT', 1, None, 0, 0),
   (9, 'review_events_sha256', 'TEXT', 1, None, 0, 0)),
  ((0,
    0,
    'sync_publication',
    'publication_id',
    'publication_id',
    'NO ACTION',
    'NO ACTION',
    'NONE'),),
  ((0,
    'sqlite_autoindex_sync_report_1',
    1,
    'pk',
    0,
    ((0, 0, 'name', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('sync_session',
  ((0, 'slot', 'INTEGER', 0, None, 1, 0),
   (1, 'session_id', 'TEXT', 1, None, 0, 0),
   (2, 'lease_token', 'TEXT', 1, None, 0, 0),
   (3, 'generation', 'INTEGER', 1, None, 0, 0),
   (4, 'lease_until', 'TEXT', 1, None, 0, 0),
   (5, 'updated_at', 'TEXT', 1, None, 0, 0),
   (6, 'metrics_json', 'TEXT', 1, "'{}'", 0, 0)),
  ((0, 0, 'sync_pair', 'slot', 'slot', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ()),
 ('sync_source_admission',
  ((0, 'admission_id', 'TEXT', 1, None, 1, 0),
   (1, 'format_version', 'INTEGER', 1, None, 0, 0),
   (2, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (3, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (4, 'target_library_id', 'TEXT', 1, None, 0, 0),
   (5, 'target_node_id', 'TEXT', 1, None, 0, 0),
   (6, 'core_sha256', 'TEXT', 1, None, 0, 0),
   (7, 'item_id', 'INTEGER', 1, None, 0, 0),
   (8, 'submitted_url_sha256', 'TEXT', 1, None, 0, 0),
   (9, 'current_home_epoch', 'INTEGER', 1, None, 0, 0),
   (10, 'receiver_library_epoch', 'INTEGER', 1, None, 0, 0),
   (11, 'recovery_generation', 'INTEGER', 1, None, 0, 0),
   (12, 'control_revision', 'INTEGER', 1, None, 0, 0),
   (13, 'previous_admission_id', 'TEXT', 0, None, 0, 0),
   (14, 'lease_generation', 'INTEGER', 1, None, 0, 0),
   (15, 'attempts', 'INTEGER', 1, None, 0, 0),
   (16, 'attempt_basis', 'TEXT', 1, None, 0, 0),
   (17, 'request_json', 'TEXT', 1, None, 0, 0),
   (18, 'request_sha256', 'TEXT', 1, None, 0, 0),
   (19, 'admission_sha256', 'TEXT', 1, None, 0, 0),
   (20, 'result_json', 'TEXT', 1, None, 0, 0),
   (21, 'result_sha256', 'TEXT', 1, None, 0, 0),
   (22, 'committed_at', 'TEXT', 1, None, 0, 0)),
  ((0,
    0,
    'sync_source_admission',
    'previous_admission_id',
    'admission_id',
    'NO ACTION',
    'NO ACTION',
    'NONE'),
   (1, 0, 'sync_queue', 'item_id', 'item_id', 'NO ACTION', 'NO ACTION', 'NONE')),
  ((0,
    'idx_source_admission_core',
    0,
    'c',
    0,
    ((0, 6, 'core_sha256', 0, 'BINARY', 1),
     (1, 7, 'item_id', 0, 'BINARY', 1),
     (2, 22, 'committed_at', 0, 'BINARY', 1),
     (3, 0, 'admission_id', 0, 'BINARY', 1),
     (4, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_source_admission_first',
    1,
    'c',
    1,
    ((0, 6, 'core_sha256', 0, 'BINARY', 1),
     (1, 7, 'item_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (2,
    'idx_source_admission_previous',
    1,
    'c',
    1,
    ((0, 13, 'previous_admission_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (3,
    'idx_source_admission_claim',
    1,
    'c',
    0,
    ((0, 7, 'item_id', 0, 'BINARY', 1),
     (1, 14, 'lease_generation', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (4,
    'sqlite_autoindex_sync_source_admission_1',
    1,
    'pk',
    0,
    ((0, 0, 'admission_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_source_continuity_activation',
  ((0, 'continuity_id', 'TEXT', 1, None, 1, 0),
   (1, 'format_version', 'INTEGER', 1, None, 0, 0),
   (2, 'owner_profile_sha256', 'TEXT', 1, None, 0, 0),
   (3, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (4, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (5, 'target_library_id', 'TEXT', 1, None, 0, 0),
   (6, 'target_node_id', 'TEXT', 1, None, 0, 0),
   (7, 'target_epoch', 'INTEGER', 1, None, 0, 0),
   (8, 'contract_json', 'TEXT', 1, None, 0, 0),
   (9, 'contract_sha256', 'TEXT', 1, None, 0, 0),
   (10, 'activated_at', 'TEXT', 1, None, 0, 0)),
  (),
  ((0,
    'idx_source_continuity_identity',
    1,
    'c',
    0,
    ((0, 2, 'owner_profile_sha256', 0, 'BINARY', 1),
     (1, 3, 'source_library_id', 0, 'BINARY', 1),
     (2, 4, 'source_node_id', 0, 'BINARY', 1),
     (3, 5, 'target_library_id', 0, 'BINARY', 1),
     (4, 6, 'target_node_id', 0, 'BINARY', 1),
     (5, 7, 'target_epoch', 0, 'BINARY', 1),
     (6, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_sync_source_continuity_activation_1',
    1,
    'pk',
    0,
    ((0, 0, 'continuity_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_source_control_disposition',
  ((0, 'request_id', 'TEXT', 1, None, 1, 0),
   (1, 'format_version', 'INTEGER', 1, None, 0, 0),
   (2, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (3, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (4, 'target_library_id', 'TEXT', 1, None, 0, 0),
   (5, 'target_node_id', 'TEXT', 1, None, 0, 0),
   (6, 'core_sha256', 'TEXT', 1, None, 0, 0),
   (7, 'item_id', 'INTEGER', 1, None, 0, 0),
   (8, 'original_request_id', 'TEXT', 1, None, 0, 0),
   (9, 'original_request_sha256', 'TEXT', 1, None, 0, 0),
   (10, 'original_kind', 'TEXT', 1, None, 0, 0),
   (11, 'original_request_json', 'TEXT', 1, None, 0, 0),
   (12, 'continuity_id', 'TEXT', 0, None, 0, 0),
   (13, 'registration_sha256', 'TEXT', 0, None, 0, 0),
   (14, 'disposition', 'TEXT', 1, None, 0, 0),
   (15, 'request_json', 'TEXT', 1, None, 0, 0),
   (16, 'request_sha256', 'TEXT', 1, None, 0, 0),
   (17, 'result_json', 'TEXT', 1, None, 0, 0),
   (18, 'result_sha256', 'TEXT', 1, None, 0, 0),
   (19, 'disposition_sha256', 'TEXT', 1, None, 0, 0),
   (20, 'defer_receipt_json', 'TEXT', 0, None, 0, 0),
   (21, 'defer_receipt_sha256', 'TEXT', 0, None, 0, 0),
   (22, 'committed_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'sync_queue', 'item_id', 'item_id', 'NO ACTION', 'NO ACTION', 'NONE'),
   (1,
    0,
    'sync_source_control_registration',
    'registration_sha256',
    'registration_sha256',
    'NO ACTION',
    'NO ACTION',
    'NONE'),
   (2,
    0,
    'sync_source_continuity_activation',
    'continuity_id',
    'continuity_id',
    'NO ACTION',
    'NO ACTION',
    'NONE')),
  ((0,
    'idx_source_disposition_core',
    0,
    'c',
    0,
    ((0, 6, 'core_sha256', 0, 'BINARY', 1),
     (1, 7, 'item_id', 0, 'BINARY', 1),
     (2, 22, 'committed_at', 0, 'BINARY', 1),
     (3, 0, 'request_id', 0, 'BINARY', 1),
     (4, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_source_disposition_original',
    1,
    'c',
    0,
    ((0, 8, 'original_request_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (2,
    'sqlite_autoindex_sync_source_control_disposition_1',
    1,
    'pk',
    0,
    ((0, 0, 'request_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_source_control_registration',
  ((0, 'request_id', 'TEXT', 1, None, 1, 0),
   (1, 'format_version', 'INTEGER', 1, None, 0, 0),
   (2, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (3, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (4, 'target_library_id', 'TEXT', 1, None, 0, 0),
   (5, 'target_node_id', 'TEXT', 1, None, 0, 0),
   (6, 'continuity_id', 'TEXT', 1, None, 0, 0),
   (7, 'core_sha256', 'TEXT', 1, None, 0, 0),
   (8, 'item_id', 'INTEGER', 1, None, 0, 0),
   (9, 'original_request_id', 'TEXT', 1, None, 0, 0),
   (10, 'original_request_sha256', 'TEXT', 1, None, 0, 0),
   (11, 'original_kind', 'TEXT', 1, None, 0, 0),
   (12, 'original_request_json', 'TEXT', 1, None, 0, 0),
   (13, 'request_json', 'TEXT', 1, None, 0, 0),
   (14, 'request_sha256', 'TEXT', 1, None, 0, 0),
   (15, 'registration_sha256', 'TEXT', 1, None, 0, 0),
   (16, 'result_json', 'TEXT', 1, None, 0, 0),
   (17, 'result_sha256', 'TEXT', 1, None, 0, 0),
   (18, 'committed_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'sync_queue', 'item_id', 'item_id', 'NO ACTION', 'NO ACTION', 'NONE'),
   (1,
    0,
    'sync_source_continuity_activation',
    'continuity_id',
    'continuity_id',
    'NO ACTION',
    'NO ACTION',
    'NONE')),
  ((0,
    'idx_source_registration_core',
    0,
    'c',
    0,
    ((0, 7, 'core_sha256', 0, 'BINARY', 1),
     (1, 8, 'item_id', 0, 'BINARY', 1),
     (2, 18, 'committed_at', 0, 'BINARY', 1),
     (3, 0, 'request_id', 0, 'BINARY', 1),
     (4, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_source_registration_sha',
    1,
    'c',
    0,
    ((0, 15, 'registration_sha256', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (2,
    'idx_source_registration_original',
    1,
    'c',
    0,
    ((0, 9, 'original_request_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (3,
    'sqlite_autoindex_sync_source_control_registration_1',
    1,
    'pk',
    0,
    ((0, 0, 'request_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('sync_upload_pin',
  ((0, 'pin_id', 'TEXT', 1, None, 1, 0),
   (1, 'kind', 'TEXT', 1, None, 0, 0),
   (2, 'publication_id', 'TEXT', 0, None, 0, 0),
   (3, 'parent_pin_id', 'TEXT', 0, None, 0, 0),
   (4, 'source_library_id', 'TEXT', 1, None, 0, 0),
   (5, 'source_node_id', 'TEXT', 1, None, 0, 0),
   (6, 'source_epoch', 'INTEGER', 0, None, 0, 0),
   (7, 'source_revision', 'INTEGER', 0, None, 0, 0),
   (8, 'target_library_id', 'TEXT', 0, None, 0, 0),
   (9, 'target_library_epoch', 'INTEGER', 0, None, 0, 0),
   (10, 'recovery_generation', 'INTEGER', 0, None, 0, 0),
   (11, 'descriptor_json', 'TEXT', 0, None, 0, 0),
   (12, 'intent_sha256', 'TEXT', 1, None, 0, 0),
   (13, 'state', 'TEXT', 1, None, 0, 0),
   (14, 'artifact_id', 'TEXT', 0, None, 0, 0),
   (15, 'abandon_operation_id', 'TEXT', 0, None, 0, 0),
   (16, 'abandon_request_sha256', 'TEXT', 0, None, 0, 0),
   (17, 'created_at', 'TEXT', 1, None, 0, 0),
   (18, 'updated_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'storage_artifact', 'artifact_id', 'artifact_id', 'NO ACTION', 'NO ACTION', 'NONE'),
   (1, 0, 'sync_upload_pin', 'parent_pin_id', 'pin_id', 'NO ACTION', 'NO ACTION', 'NONE')),
  ((0,
    'idx_upload_hold',
    0,
    'c',
    0,
    ((0, 13, 'state', 0, 'BINARY', 1),
     (1, 14, 'artifact_id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'idx_upload_abandon',
    1,
    'c',
    1,
    ((0, 15, 'abandon_operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (2,
    'idx_upload_descriptor',
    1,
    'c',
    1,
    ((0, 3, 'parent_pin_id', 0, 'BINARY', 1),
     (1, 11, 'descriptor_json', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (3,
    'idx_upload_publication',
    1,
    'c',
    1,
    ((0, 2, 'publication_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (4,
    'sqlite_autoindex_sync_upload_pin_1',
    1,
    'pk',
    0,
    ((0, 0, 'pin_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('task_request',
  ((0, 'request_id', 'TEXT', 0, None, 1, 0),
   (1, 'job_id', 'INTEGER', 1, None, 0, 0),
   (2, 'payload_signature', 'TEXT', 1, None, 0, 0),
   (3, 'created_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'job', 'job_id', 'job_id', 'NO ACTION', 'NO ACTION', 'NONE'),),
  ((0,
    'sqlite_autoindex_task_request_1',
    1,
    'pk',
    0,
    ((0, 0, 'request_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('transcript_segment',
  ((0, 'id', 'TEXT', 1, None, 2, 0),
   (1, 'video_id', 'TEXT', 0, None, 1, 0),
   (2, 'idx', 'INTEGER', 0, None, 0, 0),
   (3, 'start_ms', 'INTEGER', 0, None, 0, 0),
   (4, 'end_ms', 'INTEGER', 0, None, 0, 0),
   (5, 'text', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'idx_seg_video',
    0,
    'c',
    0,
    ((0, 1, 'video_id', 0, 'BINARY', 1),
     (1, 3, 'start_ms', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_transcript_segment_1',
    1,
    'pk',
    0,
    ((0, 1, 'video_id', 0, 'BINARY', 1),
     (1, 0, 'id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))))),
 ('user_decision',
  ((0, 'decision_id', 'INTEGER', 0, None, 1, 0),
   (1, 'atom_id', 'TEXT', 0, None, 0, 0),
   (2, 'conflict_id', 'TEXT', 0, None, 0, 0),
   (3, 'action', 'TEXT', 0, None, 0, 0),
   (4, 'revised_claim', 'TEXT', 0, None, 0, 0),
   (5, 'note', 'TEXT', 0, None, 0, 0),
   (6, 'created_at', 'TEXT', 0, None, 0, 0),
   (7, 'event_id', 'TEXT', 0, None, 0, 0),
   (8, 'client_id', 'TEXT', 0, None, 0, 0),
   (9, 'decided_at', 'TEXT', 0, None, 0, 0),
   (10, 'target_fingerprint', 'TEXT', 0, None, 0, 0),
   (11, 'target_snapshot_json', 'TEXT', 0, None, 0, 0),
   (12, 'event_schema_version', 'INTEGER', 0, None, 0, 0)),
  (),
  ((0,
    'idx_decision_event',
    1,
    'c',
    0,
    ((0, 7, 'event_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)),
 ('video_asset',
  ((0, 'video_id', 'TEXT', 0, None, 1, 0),
   (1, 'source_platform', 'TEXT', 0, None, 0, 0),
   (2, 'source_type', 'TEXT', 0, None, 0, 0),
   (3, 'source_url', 'TEXT', 0, None, 0, 0),
   (4, 'title', 'TEXT', 0, None, 0, 0),
   (5, 'author', 'TEXT', 0, None, 0, 0),
   (6, 'source_published_at', 'TEXT', 0, None, 0, 0),
   (7, 'duration_ms', 'INTEGER', 0, None, 0, 0),
   (8, 'width', 'INTEGER', 0, None, 0, 0),
   (9, 'height', 'INTEGER', 0, None, 0, 0),
   (10, 'fps', 'REAL', 0, None, 0, 0),
   (11, 'content_sha256', 'TEXT', 0, None, 0, 0),
   (12, 'audio_sha256', 'TEXT', 0, None, 0, 0),
   (13, 'imported_at', 'TEXT', 0, None, 0, 0),
   (14, 'files_json', 'TEXT', 0, None, 0, 0),
   (15, 'status', 'TEXT', 0, "'imported'", 0, 0),
   (16, 'sync_state', 'TEXT', 1, "'ready'", 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_video_asset_2',
    1,
    'u',
    0,
    ((0, 11, 'content_sha256', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_video_asset_1',
    1,
    'pk',
    0,
    ((0, 0, 'video_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))),
 ('visual_observation',
  ((0, 'id', 'TEXT', 1, None, 2, 0),
   (1, 'video_id', 'TEXT', 0, None, 1, 0),
   (2, 'frame_id', 'TEXT', 0, None, 0, 0),
   (3, 'pts_ms', 'INTEGER', 0, None, 0, 0),
   (4, 'scene_description', 'TEXT', 0, None, 0, 0),
   (5, 'materials_json', 'TEXT', 0, None, 0, 0),
   (6, 'tools_json', 'TEXT', 0, None, 0, 0),
   (7, 'subtitle_text', 'TEXT', 0, None, 0, 0),
   (8, 'measurement', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'idx_vis_video',
    0,
    'c',
    0,
    ((0, 1, 'video_id', 0, 'BINARY', 1),
     (1, 3, 'pts_ms', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_visual_observation_1',
    1,
    'pk',
    0,
    ((0, 1, 'video_id', 0, 'BINARY', 1),
     (1, 0, 'id', 0, 'BINARY', 1),
     (2, -1, None, 0, 'BINARY', 0))))),
 ('worker_lease',
  ((0, 'scope', 'TEXT', 0, None, 1, 0),
   (1, 'owner_id', 'TEXT', 0, None, 0, 0),
   (2, 'lease_until', 'INTEGER', 1, '0', 0, 0),
   (3, 'updated_at', 'TEXT', 0, None, 0, 0)),
  (),
  ((0,
    'sqlite_autoindex_worker_lease_1',
    1,
    'pk',
    0,
    ((0, 0, 'scope', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))),)))

AUTHORITY_OBJECTS = (('index',
  'idx_personal_authority_mutation_grant',
  'personal_authority_mutation_commit',
  'CREATE INDEX idx_personal_authority_mutation_grant ON '
  'personal_authority_mutation_commit(domain_id,grant_sha256,original_sequence)'),
 ('index',
  'sqlite_autoindex_personal_authority_mutation_commit_1',
  'personal_authority_mutation_commit',
  None),
 ('table',
  'personal_authority_binding',
  'personal_authority_binding',
  'CREATE TABLE personal_authority_binding (slot INTEGER PRIMARY KEY NOT NULL '
  "CHECK(typeof(slot)='integer' AND slot>=1 AND slot<=9223372036854775807 AND slot=1),\n"
  "        namespace_version INTEGER NOT NULL CHECK(typeof(namespace_version)='integer' AND "
  'namespace_version>=1 AND namespace_version<=9223372036854775807 AND namespace_version=3),\n'
  "        domain_id TEXT NOT NULL CHECK(typeof(domain_id)='text' AND length(domain_id)=36 AND "
  "substr(domain_id,9,1)='-' AND substr(domain_id,14,1)='-' AND substr(domain_id,19,1)='-' AND "
  "substr(domain_id,24,1)='-' AND length(replace(domain_id,'-',''))=32 AND "
  "replace(domain_id,'-','') NOT GLOB '*[^0-9a-f]*' AND "
  "domain_id<>'00000000-0000-0000-0000-000000000000'),\n"
  "        facility_id TEXT NOT NULL CHECK(typeof(facility_id)='text' AND length(facility_id)=36 "
  "AND substr(facility_id,9,1)='-' AND substr(facility_id,14,1)='-' AND "
  "substr(facility_id,19,1)='-' AND substr(facility_id,24,1)='-' AND "
  "length(replace(facility_id,'-',''))=32 AND replace(facility_id,'-','') NOT GLOB '*[^0-9a-f]*' "
  "AND facility_id<>'00000000-0000-0000-0000-000000000000'),\n"
  "        owner_node_id TEXT NOT NULL CHECK(typeof(owner_node_id)='text' AND "
  "length(owner_node_id)=36 AND substr(owner_node_id,9,1)='-' AND substr(owner_node_id,14,1)='-' "
  "AND substr(owner_node_id,19,1)='-' AND substr(owner_node_id,24,1)='-' AND "
  "length(replace(owner_node_id,'-',''))=32 AND replace(owner_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*' AND owner_node_id<>'00000000-0000-0000-0000-000000000000'),\n"
  "        epoch INTEGER NOT NULL CHECK(typeof(epoch)='integer' AND epoch>=1 AND "
  'epoch<=9223372036854775807),\n'
  "        current_grant_sha256 TEXT NOT NULL CHECK(typeof(current_grant_sha256)='text' AND "
  "length(current_grant_sha256)=64 AND current_grant_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "        init_operation_id TEXT NOT NULL CHECK(typeof(init_operation_id)='text' AND "
  "length(init_operation_id)=36 AND substr(init_operation_id,9,1)='-' AND "
  "substr(init_operation_id,14,1)='-' AND substr(init_operation_id,19,1)='-' AND "
  "substr(init_operation_id,24,1)='-' AND length(replace(init_operation_id,'-',''))=32 AND "
  "replace(init_operation_id,'-','') NOT GLOB '*[^0-9a-f]*' AND "
  "init_operation_id<>'00000000-0000-0000-0000-000000000000'),\n"
  "        activation_prepare_sha256 TEXT NOT NULL CHECK(typeof(activation_prepare_sha256)='text' "
  'AND length(activation_prepare_sha256)=64 AND activation_prepare_sha256 NOT GLOB '
  "'*[^0-9a-f]*'),\n"
  "        revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>=1 AND "
  'revision<=9223372036854775807))'),
 ('table',
  'personal_authority_mutation_commit',
  'personal_authority_mutation_commit',
  'CREATE TABLE personal_authority_mutation_commit (operation_id TEXT PRIMARY KEY NOT NULL '
  'REFERENCES mutation_receipt(operation_id)\n'
  "            CHECK(typeof(operation_id)='text' AND length(operation_id)=36 AND "
  "substr(operation_id,9,1)='-' AND substr(operation_id,14,1)='-' AND "
  "substr(operation_id,19,1)='-' AND substr(operation_id,24,1)='-' AND "
  "length(replace(operation_id,'-',''))=32 AND replace(operation_id,'-','') NOT GLOB '*[^0-9a-f]*' "
  "AND operation_id<>'00000000-0000-0000-0000-000000000000'),\n"
  "        journal_version INTEGER NOT NULL CHECK(typeof(journal_version)='integer' AND "
  'journal_version>=1 AND journal_version<=9223372036854775807 AND journal_version=2),\n'
  "        namespace_version INTEGER NOT NULL CHECK(typeof(namespace_version)='integer' AND "
  'namespace_version>=1 AND namespace_version<=9223372036854775807 AND namespace_version=3),\n'
  "        mutation_codec_version INTEGER NOT NULL CHECK(typeof(mutation_codec_version)='integer' "
  'AND mutation_codec_version>=1 AND mutation_codec_version<=9223372036854775807 AND '
  'mutation_codec_version=3),\n'
  '        binding_slot INTEGER NOT NULL REFERENCES personal_authority_binding(slot) '
  "CHECK(typeof(binding_slot)='integer' AND binding_slot>=1 AND binding_slot<=9223372036854775807 "
  'AND binding_slot=1),\n'
  "        domain_id TEXT NOT NULL CHECK(typeof(domain_id)='text' AND length(domain_id)=36 AND "
  "substr(domain_id,9,1)='-' AND substr(domain_id,14,1)='-' AND substr(domain_id,19,1)='-' AND "
  "substr(domain_id,24,1)='-' AND length(replace(domain_id,'-',''))=32 AND "
  "replace(domain_id,'-','') NOT GLOB '*[^0-9a-f]*' AND "
  "domain_id<>'00000000-0000-0000-0000-000000000000'),\n"
  "        facility_id TEXT NOT NULL CHECK(typeof(facility_id)='text' AND length(facility_id)=36 "
  "AND substr(facility_id,9,1)='-' AND substr(facility_id,14,1)='-' AND "
  "substr(facility_id,19,1)='-' AND substr(facility_id,24,1)='-' AND "
  "length(replace(facility_id,'-',''))=32 AND replace(facility_id,'-','') NOT GLOB '*[^0-9a-f]*' "
  "AND facility_id<>'00000000-0000-0000-0000-000000000000'),\n"
  "        owner_node_id TEXT NOT NULL CHECK(typeof(owner_node_id)='text' AND "
  "length(owner_node_id)=36 AND substr(owner_node_id,9,1)='-' AND substr(owner_node_id,14,1)='-' "
  "AND substr(owner_node_id,19,1)='-' AND substr(owner_node_id,24,1)='-' AND "
  "length(replace(owner_node_id,'-',''))=32 AND replace(owner_node_id,'-','') NOT GLOB "
  "'*[^0-9a-f]*' AND owner_node_id<>'00000000-0000-0000-0000-000000000000'),\n"
  "        epoch INTEGER NOT NULL CHECK(typeof(epoch)='integer' AND epoch>=1 AND "
  'epoch<=9223372036854775807),\n'
  "        grant_sha256 TEXT NOT NULL CHECK(typeof(grant_sha256)='text' AND "
  "length(grant_sha256)=64 AND grant_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "        binding_revision INTEGER NOT NULL CHECK(typeof(binding_revision)='integer' AND "
  'binding_revision>=1 AND binding_revision<=9223372036854775807),\n'
  "        original_sequence INTEGER NOT NULL CHECK(typeof(original_sequence)='integer' AND "
  'original_sequence>=1 AND original_sequence<=9223372036854775807),\n'
  "        plan_sha256 TEXT NOT NULL CHECK(typeof(plan_sha256)='text' AND length(plan_sha256)=64 "
  "AND plan_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "        prepare_sha256 TEXT NOT NULL CHECK(typeof(prepare_sha256)='text' AND "
  "length(prepare_sha256)=64 AND prepare_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "        authority_sha256 TEXT NOT NULL CHECK(typeof(authority_sha256)='text' AND "
  "length(authority_sha256)=64 AND authority_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  '        authority_store_header_sha256 TEXT NOT NULL '
  "CHECK(typeof(authority_store_header_sha256)='text' AND length(authority_store_header_sha256)=64 "
  "AND authority_store_header_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "        scope TEXT NOT NULL CHECK(typeof(scope)='text' AND length(scope)>=1 AND "
  'length(CAST(scope AS BLOB))<=256 AND instr(scope,char(0))=0),\n'
  "        request_hash TEXT NOT NULL CHECK(typeof(request_hash)='text' AND "
  "length(request_hash)=64 AND request_hash NOT GLOB '*[^0-9a-f]*'),\n"
  "        declared_effect_sha256 TEXT NOT NULL CHECK(typeof(declared_effect_sha256)='text' AND "
  "length(declared_effect_sha256)=64 AND declared_effect_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "        receipt_row_sha256 TEXT NOT NULL CHECK(typeof(receipt_row_sha256)='text' AND "
  "length(receipt_row_sha256)=64 AND receipt_row_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "        history_rows_sha256 TEXT NOT NULL CHECK(typeof(history_rows_sha256)='text' AND "
  "length(history_rows_sha256)=64 AND history_rows_sha256 NOT GLOB '*[^0-9a-f]*'),\n"
  "        response_status INTEGER NOT NULL CHECK(typeof(response_status)='integer' AND "
  'response_status>=200 AND response_status<=9223372036854775807 AND response_status<=299),\n'
  "        response_json TEXT NOT NULL CHECK(typeof(response_json)='text' AND "
  'length(response_json)>=1 AND length(CAST(response_json AS BLOB))<=16777216 AND '
  'instr(response_json,char(0))=0),\n'
  "        created_at TEXT NOT NULL CHECK(typeof(created_at)='text' AND length(created_at)>=1 AND "
  'length(CAST(created_at AS BLOB))<=40 AND instr(created_at,char(0))=0))'),
 ('trigger',
  'trg_personal_authority_binding_delete',
  'personal_authority_binding',
  'CREATE TRIGGER trg_personal_authority_binding_delete BEFORE DELETE ON '
  "personal_authority_binding BEGIN SELECT RAISE(ABORT,'PERSONAL_AUTHORITY_BINDING_IMMUTABLE'); "
  'END'),
 ('trigger',
  'trg_personal_authority_binding_insert',
  'personal_authority_binding',
  'CREATE TRIGGER trg_personal_authority_binding_insert BEFORE INSERT ON '
  'personal_authority_binding\n'
  '        WHEN EXISTS(SELECT 1 FROM personal_authority_binding WHERE slot=NEW.slot)\n'
  "        BEGIN SELECT RAISE(ABORT,'PERSONAL_AUTHORITY_BINDING_KEY_REUSED'); END"),
 ('trigger',
  'trg_personal_authority_binding_update',
  'personal_authority_binding',
  'CREATE TRIGGER trg_personal_authority_binding_update BEFORE UPDATE ON '
  'personal_authority_binding\n'
  '        WHEN NEW.slot IS NOT OLD.slot OR NEW.namespace_version IS NOT OLD.namespace_version\n'
  '            OR NEW.domain_id IS NOT OLD.domain_id OR NEW.facility_id IS NOT OLD.facility_id\n'
  '            OR NEW.owner_node_id IS NOT OLD.owner_node_id OR NEW.init_operation_id IS NOT '
  'OLD.init_operation_id\n'
  '            OR NEW.revision<>OLD.revision+1 OR NEW.epoch<=OLD.epoch\n'
  '            OR NEW.current_grant_sha256 IS OLD.current_grant_sha256\n'
  '            OR NEW.activation_prepare_sha256 IS OLD.activation_prepare_sha256\n'
  "        BEGIN SELECT RAISE(ABORT,'PERSONAL_AUTHORITY_BINDING_CAS_REQUIRED'); END"),
 ('trigger',
  'trg_personal_authority_mutation_delete',
  'personal_authority_mutation_commit',
  'CREATE TRIGGER trg_personal_authority_mutation_delete BEFORE DELETE ON '
  'personal_authority_mutation_commit BEGIN SELECT '
  "RAISE(ABORT,'PERSONAL_AUTHORITY_MUTATION_IMMUTABLE'); END"),
 ('trigger',
  'trg_personal_authority_mutation_insert',
  'personal_authority_mutation_commit',
  'CREATE TRIGGER trg_personal_authority_mutation_insert BEFORE INSERT ON '
  'personal_authority_mutation_commit\n'
  '        WHEN EXISTS(SELECT 1 FROM personal_authority_mutation_commit WHERE '
  'operation_id=NEW.operation_id)\n'
  '            OR NOT EXISTS(SELECT 1 FROM personal_authority_binding WHERE slot=NEW.binding_slot\n'
  '                AND namespace_version=NEW.namespace_version AND domain_id=NEW.domain_id\n'
  '                AND facility_id=NEW.facility_id AND owner_node_id=NEW.owner_node_id\n'
  '                AND epoch=NEW.epoch AND current_grant_sha256=NEW.grant_sha256\n'
  '                AND revision=NEW.binding_revision)\n'
  '            OR NOT EXISTS(SELECT 1 FROM mutation_receipt WHERE operation_id=NEW.operation_id\n'
  '                AND scope=NEW.scope AND request_hash=NEW.request_hash\n'
  '                AND response_status=NEW.response_status AND response_json=NEW.response_json\n'
  '                AND created_at=NEW.created_at)\n'
  "        BEGIN SELECT RAISE(ABORT,'PERSONAL_AUTHORITY_MUTATION_BINDING_INVALID'); END"),
 ('trigger',
  'trg_personal_authority_mutation_update',
  'personal_authority_mutation_commit',
  'CREATE TRIGGER trg_personal_authority_mutation_update BEFORE UPDATE ON '
  'personal_authority_mutation_commit BEGIN SELECT '
  "RAISE(ABORT,'PERSONAL_AUTHORITY_MUTATION_IMMUTABLE'); END"))

AUTHORITY_TABLE_CONTRACTS = (('personal_authority_binding',
  ((0, 'slot', 'INTEGER', 1, None, 1, 0),
   (1, 'namespace_version', 'INTEGER', 1, None, 0, 0),
   (2, 'domain_id', 'TEXT', 1, None, 0, 0),
   (3, 'facility_id', 'TEXT', 1, None, 0, 0),
   (4, 'owner_node_id', 'TEXT', 1, None, 0, 0),
   (5, 'epoch', 'INTEGER', 1, None, 0, 0),
   (6, 'current_grant_sha256', 'TEXT', 1, None, 0, 0),
   (7, 'init_operation_id', 'TEXT', 1, None, 0, 0),
   (8, 'activation_prepare_sha256', 'TEXT', 1, None, 0, 0),
   (9, 'revision', 'INTEGER', 1, None, 0, 0)),
  (),
  ()),
 ('personal_authority_mutation_commit',
  ((0, 'operation_id', 'TEXT', 1, None, 1, 0),
   (1, 'journal_version', 'INTEGER', 1, None, 0, 0),
   (2, 'namespace_version', 'INTEGER', 1, None, 0, 0),
   (3, 'mutation_codec_version', 'INTEGER', 1, None, 0, 0),
   (4, 'binding_slot', 'INTEGER', 1, None, 0, 0),
   (5, 'domain_id', 'TEXT', 1, None, 0, 0),
   (6, 'facility_id', 'TEXT', 1, None, 0, 0),
   (7, 'owner_node_id', 'TEXT', 1, None, 0, 0),
   (8, 'epoch', 'INTEGER', 1, None, 0, 0),
   (9, 'grant_sha256', 'TEXT', 1, None, 0, 0),
   (10, 'binding_revision', 'INTEGER', 1, None, 0, 0),
   (11, 'original_sequence', 'INTEGER', 1, None, 0, 0),
   (12, 'plan_sha256', 'TEXT', 1, None, 0, 0),
   (13, 'prepare_sha256', 'TEXT', 1, None, 0, 0),
   (14, 'authority_sha256', 'TEXT', 1, None, 0, 0),
   (15, 'authority_store_header_sha256', 'TEXT', 1, None, 0, 0),
   (16, 'scope', 'TEXT', 1, None, 0, 0),
   (17, 'request_hash', 'TEXT', 1, None, 0, 0),
   (18, 'declared_effect_sha256', 'TEXT', 1, None, 0, 0),
   (19, 'receipt_row_sha256', 'TEXT', 1, None, 0, 0),
   (20, 'history_rows_sha256', 'TEXT', 1, None, 0, 0),
   (21, 'response_status', 'INTEGER', 1, None, 0, 0),
   (22, 'response_json', 'TEXT', 1, None, 0, 0),
   (23, 'created_at', 'TEXT', 1, None, 0, 0)),
  ((0, 0, 'personal_authority_binding', 'binding_slot', 'slot', 'NO ACTION', 'NO ACTION', 'NONE'),
   (1, 0, 'mutation_receipt', 'operation_id', 'operation_id', 'NO ACTION', 'NO ACTION', 'NONE')),
  ((0,
    'idx_personal_authority_mutation_grant',
    0,
    'c',
    0,
    ((0, 5, 'domain_id', 0, 'BINARY', 1),
     (1, 9, 'grant_sha256', 0, 'BINARY', 1),
     (2, 11, 'original_sequence', 0, 'BINARY', 1),
     (3, -1, None, 0, 'BINARY', 0))),
   (1,
    'sqlite_autoindex_personal_authority_mutation_commit_1',
    1,
    'pk',
    0,
    ((0, 0, 'operation_id', 0, 'BINARY', 1), (1, -1, None, 0, 'BINARY', 0))))))

NULL_SQL_OBJECTS = (('index', 'sqlite_autoindex_budget_attempt_1', 'budget_attempt'),
 ('index', 'sqlite_autoindex_budget_operation_1', 'budget_operation'),
 ('index', 'sqlite_autoindex_checklist_check_1', 'checklist_check'),
 ('index', 'sqlite_autoindex_checklist_check_2', 'checklist_check'),
 ('index', 'sqlite_autoindex_checklist_check_3', 'checklist_check'),
 ('index', 'sqlite_autoindex_checklist_instance_1', 'checklist_instance'),
 ('index', 'sqlite_autoindex_checklist_item_1', 'checklist_item'),
 ('index', 'sqlite_autoindex_conflict_case_1', 'conflict_case'),
 ('index', 'sqlite_autoindex_decision_card_1', 'decision_card'),
 ('index', 'sqlite_autoindex_frame_1', 'frame'),
 ('index',
  'sqlite_autoindex_home_knowledge_recovery_operation_1',
  'home_knowledge_recovery_operation'),
 ('index', 'sqlite_autoindex_knowledge_atom_1', 'knowledge_atom'),
 ('index', 'sqlite_autoindex_knowledge_cluster_1', 'knowledge_cluster'),
 ('index', 'sqlite_autoindex_meta_1', 'meta'),
 ('index', 'sqlite_autoindex_mutation_receipt_1', 'mutation_receipt'),
 ('index', 'sqlite_autoindex_ocr_span_1', 'ocr_span'),
 ('index',
  'sqlite_autoindex_personal_authority_mutation_commit_1',
  'personal_authority_mutation_commit'),
 ('index', 'sqlite_autoindex_personal_evidence_ref_1', 'personal_evidence_ref'),
 ('index', 'sqlite_autoindex_personal_revision_1', 'personal_revision'),
 ('index', 'sqlite_autoindex_renovation_project_1', 'renovation_project'),
 ('index', 'sqlite_autoindex_source_attachment_1', 'source_attachment'),
 ('index', 'sqlite_autoindex_source_attachment_2', 'source_attachment'),
 ('index', 'sqlite_autoindex_source_attachment_3', 'source_attachment'),
 ('index', 'sqlite_autoindex_source_record_1', 'source_record'),
 ('index', 'sqlite_autoindex_storage_artifact_1', 'storage_artifact'),
 ('index', 'sqlite_autoindex_storage_artifact_2', 'storage_artifact'),
 ('index', 'sqlite_autoindex_storage_gc_item_1', 'storage_gc_item'),
 ('index', 'sqlite_autoindex_storage_gc_operation_1', 'storage_gc_operation'),
 ('index', 'sqlite_autoindex_sync_checkpoint_1', 'sync_checkpoint'),
 ('index', 'sqlite_autoindex_sync_checkpoint_2', 'sync_checkpoint'),
 ('index', 'sqlite_autoindex_sync_checkpoint_3', 'sync_checkpoint'),
 ('index', 'sqlite_autoindex_sync_checkpoint_retirement_1', 'sync_checkpoint_retirement'),
 ('index', 'sqlite_autoindex_sync_publication_1', 'sync_publication'),
 ('index', 'sqlite_autoindex_sync_publication_fence_1', 'sync_publication_fence'),
 ('index', 'sqlite_autoindex_sync_published_video_1', 'sync_published_video'),
 ('index', 'sqlite_autoindex_sync_queue_budget_receipt_1', 'sync_queue_budget_receipt'),
 ('index', 'sqlite_autoindex_sync_queue_publication_1', 'sync_queue_publication'),
 ('index', 'sqlite_autoindex_sync_queue_source_receipt_1', 'sync_queue_source_receipt'),
 ('index', 'sqlite_autoindex_sync_recovery_checkpoint_1', 'sync_recovery_checkpoint'),
 ('index', 'sqlite_autoindex_sync_recovery_checkpoint_2', 'sync_recovery_checkpoint'),
 ('index', 'sqlite_autoindex_sync_recovery_checkpoint_3', 'sync_recovery_checkpoint'),
 ('index', 'sqlite_autoindex_sync_recovery_operation_1', 'sync_recovery_operation'),
 ('index', 'sqlite_autoindex_sync_report_1', 'sync_report'),
 ('index', 'sqlite_autoindex_sync_source_admission_1', 'sync_source_admission'),
 ('index',
  'sqlite_autoindex_sync_source_continuity_activation_1',
  'sync_source_continuity_activation'),
 ('index', 'sqlite_autoindex_sync_source_control_disposition_1', 'sync_source_control_disposition'),
 ('index',
  'sqlite_autoindex_sync_source_control_registration_1',
  'sync_source_control_registration'),
 ('index', 'sqlite_autoindex_sync_upload_pin_1', 'sync_upload_pin'),
 ('index', 'sqlite_autoindex_task_request_1', 'task_request'),
 ('index', 'sqlite_autoindex_transcript_segment_1', 'transcript_segment'),
 ('index', 'sqlite_autoindex_video_asset_1', 'video_asset'),
 ('index', 'sqlite_autoindex_video_asset_2', 'video_asset'),
 ('index', 'sqlite_autoindex_visual_observation_1', 'visual_observation'),
 ('index', 'sqlite_autoindex_worker_lease_1', 'worker_lease'))

REFERENCE_SOURCE_PINS = (('data/t16-h01-joint-sql-reference-a-r891/qa-run-01/pre-catalog.json',
  713346,
  '0a608a60509090a0642f572c2208dde8d878aa8911343c42f281a55aacadfa72'),
 ('data/t16-h01-joint-sql-reference-a-r891/qa-run-01/post-catalog.json',
  765559,
  '2c03ab6ae1bc37d653ad0de1b73dbe008a4525d7db9da6dcd1db3ab6dac5daf9'),
 ('data/t16-h01-joint-sql-reference-b-r893/qa-run-01/pre-catalog.json',
  713346,
  '4e4664237c5d64a8da89efdd468f67b26adb4293ad036b2c989b14a47885f4f3'),
 ('data/t16-h01-joint-sql-reference-b-r893/qa-run-01/post-catalog.json',
  765559,
  'fbf3940ca8366bdbd549e8301e368b088bea9018ef48c487f2d5a831aec9e17a'),
 ('data/t16-h01-joint-sql-schema-r889/design/AUTHORITY-LITERALS-01.json',
  34646,
  'fb7a0a6f3488e80e1bde9fee0946ccdfc234337062190fd39d0b2a2c643c174d'),
 ('data/t16-h01-joint-sql-reference-a-r891/ACTUAL-DELIVERY-01.json',
  4514,
  '907e3a28cb7d412ed9317eed157838833bec3028d613051fc47610b2573885b0'),
 ('data/t16-h01-joint-sql-reference-b-r893/ACTUAL-DELIVERY-01.json',
  4508,
  '09c9f4ef657dcfe36e2b179cac695aff3923a834c52d39df792b6ab5e185ccad'),
 ('data/t16-h01-joint-observation-implementation-r897/review-root/DESIGN-REVIEW-01.json',
  2227,
  '5b0429cab32876d1b8b1a54bec3b753ea005a8598062841762ade552e80dde9e'))
