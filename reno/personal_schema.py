"""Personal-domain schema installed only by an explicit migration transaction."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import uuid

from . import config
from .review import utc_now

TABLES = {
    "renovation_project": """project_id TEXT PRIMARY KEY, name TEXT NOT NULL,
        profile_json TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT""",
    "decision_card": """card_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES renovation_project(project_id),
        title TEXT NOT NULL, question TEXT NOT NULL DEFAULT '', context_json TEXT NOT NULL DEFAULT '{}',
        space TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '',
        alternatives_json TEXT NOT NULL DEFAULT '[]', selected_option_id TEXT, decision_text TEXT NOT NULL DEFAULT '',
        decision_reason TEXT NOT NULL DEFAULT '', open_questions_json TEXT NOT NULL DEFAULT '[]',
        content_snapshot_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'considering'
        CHECK(state IN ('considering','adopted','not_adopted','archived')), revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT""",
    "source_record": """source_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES renovation_project(project_id),
        source_type TEXT NOT NULL CHECK(source_type IN ('manual_note','standard_record','product_document','quote','site_photo')),
        title TEXT NOT NULL, publisher TEXT NOT NULL DEFAULT '', document_no TEXT NOT NULL DEFAULT '',
        edition TEXT NOT NULL DEFAULT '', published_on TEXT NOT NULL DEFAULT '', valid_from TEXT NOT NULL DEFAULT '',
        valid_to TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '', locator TEXT NOT NULL DEFAULT '',
        excerpt TEXT NOT NULL DEFAULT '', scope_json TEXT NOT NULL DEFAULT '{}',
        verification_state TEXT NOT NULL DEFAULT 'unverified' CHECK(verification_state IN ('unverified','user_checked')),
        validity TEXT NOT NULL DEFAULT 'unknown' CHECK(validity IN ('unknown','current','superseded','withdrawn')),
        checked_at TEXT, check_note TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL""",
    "source_attachment": """attachment_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source_record(source_id),
        project_id TEXT NOT NULL REFERENCES renovation_project(project_id), sha256 TEXT, byte_size INTEGER,
        media_type TEXT NOT NULL, original_name TEXT NOT NULL, storage_key TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK(state IN ('staging','ready','failed')), created_at TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE, error TEXT""",
    "personal_evidence_ref": """ref_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES renovation_project(project_id),
        owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
        fingerprint_version INTEGER NOT NULL DEFAULT 1, target_fingerprint TEXT, snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL""",
    "checklist_instance": """instance_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES renovation_project(project_id),
        title TEXT NOT NULL, space TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '', basis_kb_revision INTEGER,
        lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK(lifecycle_state IN ('active','archived')),
        revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT""",
    "checklist_item": """item_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES checklist_instance(instance_id),
        title TEXT NOT NULL, conditions_json TEXT NOT NULL DEFAULT '{}', method_text TEXT NOT NULL DEFAULT '',
        basis_kind TEXT NOT NULL, criteria_json TEXT NOT NULL DEFAULT '{}',
        criteria_state TEXT NOT NULL DEFAULT 'needs_verification' CHECK(criteria_state IN ('known','needs_verification')),
        position INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL""",
    "checklist_check": """check_id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES checklist_item(item_id),
        item_revision INTEGER NOT NULL, criteria_snapshot_hash TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE, commit_seq INTEGER NOT NULL,
        record_kind TEXT NOT NULL CHECK(record_kind IN ('inspection','remediation','void')), result TEXT,
        measurements_json TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
        attachment_refs_json TEXT NOT NULL DEFAULT '[]', checked_at TEXT NOT NULL, created_at TEXT NOT NULL,
        supersedes_check_id TEXT REFERENCES checklist_check(check_id), UNIQUE(item_id,commit_seq)""",
    "personal_revision": """entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL, operation_id TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY(entity_kind,entity_id,revision)""",
    "mutation_receipt": """operation_id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_hash TEXT NOT NULL,
        response_status INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL""",
}
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_personal_card_space_stage ON decision_card(project_id,space,stage,updated_at,card_id)",
    "CREATE INDEX IF NOT EXISTS idx_personal_card ON decision_card(project_id,state,updated_at,card_id)",
    "CREATE INDEX IF NOT EXISTS idx_personal_source ON source_record(project_id,updated_at,source_id)",
    "CREATE INDEX IF NOT EXISTS idx_personal_ref_target ON personal_evidence_ref(target_kind,target_id)",
    "CREATE INDEX IF NOT EXISTS idx_personal_ref_owner ON personal_evidence_ref(owner_kind,owner_id)",
    "CREATE INDEX IF NOT EXISTS idx_personal_item ON checklist_item(instance_id,position,item_id)",
    "CREATE INDEX IF NOT EXISTS idx_personal_check ON checklist_check(item_id,created_at,check_id)",
    "CREATE INDEX IF NOT EXISTS idx_personal_attachment_source ON source_attachment(source_id,state)",
]


def install(con):
    """DDL only, no commit. Safe to call twice inside a migration transaction."""
    for name, columns in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")
    for statement in INDEXES:
        con.execute(statement)


def validate(con):
    """Read-only structural validation; raises sqlite3.DatabaseError on drift."""
    reference = sqlite3.connect(":memory:")
    try:
        install(reference)
        for name in TABLES:
            expected = {row[1] for row in reference.execute(f"PRAGMA table_info({name})")}
            actual = {row[1] for row in con.execute(f"PRAGMA table_info({name})")}
            if not expected <= actual:
                raise sqlite3.DatabaseError(f"Personal schema missing columns: {name}")
    finally:
        reference.close()


def node_identity(data_dir: Path, *, create: bool = False) -> str | None:
    """Node identity is local to the DATA directory and is never in the DB."""
    path = Path(data_dir) / "node-identity.json"
    if not path.exists():
        if not create:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump({"instance_id": str(uuid.uuid4())}, stream)
                stream.flush()
                import os
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
    try:
        return str(uuid.UUID(json.loads(path.read_text(encoding="utf-8"))["instance_id"]))
    except (ValueError, TypeError, KeyError):
        raise sqlite3.DatabaseError("Invalid local node identity; refusing to generate a replacement") from None


def seed(con, data_dir: Path | None = None):
    """Explicit initialization only: establish primary and ONE default project.

    Call install+seed inside migration's transaction. Existing metadata always
    wins, including a primary from another node; copying a DB never promotes it.
    """
    if data_dir is None:
        data_dir = Path(con.execute("PRAGMA database_list").fetchone()[2]).parent
    instance_id = node_identity(data_dir, create=True)
    role = str(config.get("node_role") or "all").lower()
    primary = instance_id if role in {"all", "home"} else ""
    con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('personal_primary_id',?)", (primary,))
    con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('primary_epoch','1')")
    if role not in {"all", "home"}:
        return
    if con.execute("SELECT value FROM meta WHERE key='default_project_id'").fetchone():
        return
    project_id, now = str(uuid.uuid4()), utc_now()
    con.execute("INSERT INTO renovation_project(project_id,name,profile_json,revision,created_at,updated_at) "
                "VALUES(?,?,?,1,?,?)", (project_id, "我的装修", "{}", now, now))
    con.execute("INSERT INTO meta(key,value) VALUES('default_project_id',?)", (project_id,))
    snap = {"project_id": project_id, "name": "我的装修", "profile": {}, "revision": 1,
            "created_at": now, "updated_at": now, "archived_at": None}
    con.execute("INSERT INTO personal_revision(entity_kind,entity_id,revision,snapshot_json,created_at) "
                "VALUES('project',?,1,?,?)", (project_id, json.dumps(snap, ensure_ascii=False), now))
