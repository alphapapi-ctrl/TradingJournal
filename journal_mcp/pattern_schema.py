"""Additive pattern tables and upgrade of existing evidence ownership."""
from journal_mcp.pattern_models import REFLECTIONS

TABLES = {
    "psychology_patterns": """pattern_key TEXT NOT NULL UNIQUE, system_key TEXT, label TEXT NOT NULL,
        description TEXT, tags TEXT NOT NULL DEFAULT '[]', practice_response TEXT,
        status TEXT NOT NULL DEFAULT 'active', revision INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))""",
    "pattern_entries": """entry_key TEXT NOT NULL UNIQUE, account_id INTEGER NOT NULL,
        event_date TEXT NOT NULL, timezone TEXT NOT NULL, occurred_at TEXT, pattern_id INTEGER,
        title TEXT, session_id INTEGER, candidate_id INTEGER, trade_ids TEXT NOT NULL DEFAULT '[]',
        position_id INTEGER, parent_journal_entry_id INTEGER, context_journal_entry_id INTEGER,
        symbol TEXT, setup_description TEXT,""" + ','.join(field + " TEXT" for field in REFLECTIONS) + """,
        observed_result TEXT, next_session_intention TEXT, interpretation TEXT,
        trigger_tags TEXT NOT NULL DEFAULT '[]', emotion_tags TEXT NOT NULL DEFAULT '[]', behaviour_tags TEXT NOT NULL DEFAULT '[]',
        urge_response TEXT NOT NULL DEFAULT 'unknown', plan_adherence TEXT NOT NULL DEFAULT 'unknown',
        rule_breach TEXT NOT NULL DEFAULT 'unknown', rule_reference TEXT,
        status TEXT NOT NULL DEFAULT 'draft', follow_up_status TEXT NOT NULL DEFAULT 'pending',
        related_pattern_entry_ids TEXT NOT NULL DEFAULT '[]', management_event_ids TEXT NOT NULL DEFAULT '[]',
        evidence_ids TEXT NOT NULL DEFAULT '[]', source TEXT, recorded_by TEXT,
        field_provenance TEXT NOT NULL DEFAULT '{}', field_states TEXT NOT NULL DEFAULT '{}',
        template_id INTEGER, template_version TEXT, baseline_version TEXT, playbook_version TEXT,
        uncalibrated_score_note TEXT, proposed_rule TEXT, proposed_rule_status TEXT,
        adoption_decision TEXT, adoption_reference TEXT, adopted_at TEXT,
        legacy_journal_entry_id INTEGER UNIQUE, legacy_source_revision INTEGER, migration_id TEXT,
        legacy_reflection_text TEXT, migration_review_required INTEGER NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT (datetime('now'))""",
    "pattern_followups": """pattern_entry_id INTEGER NOT NULL, followup_key TEXT NOT NULL UNIQUE,
        observed_at TEXT, note TEXT NOT NULL, response_practised INTEGER, response_helpful INTEGER,
        observed_result TEXT, next_session_intention TEXT, evidence_ids TEXT NOT NULL DEFAULT '[]',
        source TEXT, recorded_by TEXT, field_provenance TEXT NOT NULL DEFAULT '{}',
        corrects_followup_id INTEGER UNIQUE, revision INTEGER NOT NULL DEFAULT 1""",
    "pattern_revisions": """entity TEXT NOT NULL, record_id INTEGER NOT NULL,
        revision INTEGER NOT NULL, snapshot TEXT NOT NULL, UNIQUE(entity,record_id,revision)""",
}


def migrate(conn):
    for table, fields in TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL DEFAULT (datetime('now')), {fields})")
    evidence_columns = {row[1]: row for row in conn.execute("PRAGMA table_info(evidence)")}
    if "pattern_entry_id" not in evidence_columns:
        # SQLite cannot remove NOT NULL with ALTER COLUMN. Keep IDs/BLOB links.
        for name in ("neo_immutable_evidence_UPDATE", "neo_immutable_evidence_DELETE", "neo_retain_journal_entries", "neo_retain_trades"):
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute("ALTER TABLE evidence RENAME TO evidence_before_patterns")
        conn.execute("""CREATE TABLE evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            journal_entry_id INTEGER, candidate_id INTEGER, trade_id INTEGER, pattern_entry_id INTEGER,
            filename TEXT NOT NULL, mime_type TEXT NOT NULL, sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL, stage TEXT NOT NULL, captured_at TEXT NOT NULL,
            symbol TEXT NOT NULL, timeframe TEXT NOT NULL, source TEXT NOT NULL, chart_context TEXT,
            CHECK(journal_entry_id IS NOT NULL OR pattern_entry_id IS NOT NULL))""")
        columns = ','.join(evidence_columns)
        conn.execute(f"INSERT INTO evidence ({columns}) SELECT {columns} FROM evidence_before_patterns")
        conn.execute("DROP TABLE evidence_before_patterns")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS pattern_evidence_capture ON evidence(COALESCE(journal_entry_id,-1),COALESCE(pattern_entry_id,-1),sha256,stage,captured_at)")
    for table in ("psychology_patterns", "pattern_entries"):
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        snapshot = "json_object(" + ','.join(f"'{col}',NEW.{col}" for col in cols[:40]) + ")"
        for index in range(40, len(cols), 40):
            arguments = ','.join(f"'$.{col}',NEW.{col}" for col in cols[index:index + 40])
            snapshot = f"json_set({snapshot},{arguments})"
        for action in ("INSERT", "UPDATE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS pattern_audit_{table}_{action} AFTER {action} ON {table}
                BEGIN INSERT INTO pattern_revisions(entity,record_id,revision,snapshot) VALUES ('{table}',NEW.id,NEW.revision,{snapshot}); END""")
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS pattern_revision_{table} BEFORE UPDATE ON {table}
            WHEN NEW.revision<>OLD.revision+1 BEGIN SELECT RAISE(ABORT,'Pattern edits require the next revision'); END""")
    for table in TABLES:
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS pattern_retain_{table} BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT,'Pattern history is retained; archive instead'); END""")
    for table in ("pattern_followups", "pattern_revisions"):
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS pattern_immutable_{table} BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT,'Append-only pattern history; append a correction'); END""")
    refs = {
        "accounts": "account_id=OLD.id", "trading_sessions": "session_id=OLD.id",
        "candidates": "candidate_id=OLD.id", "positions": "position_id=OLD.id",
        "journal_entries": "parent_journal_entry_id=OLD.id OR context_journal_entry_id=OLD.id OR legacy_journal_entry_id=OLD.id",
        "trades": "EXISTS(SELECT 1 FROM json_each(trade_ids) WHERE value=OLD.id)",
        "management_events": "EXISTS(SELECT 1 FROM json_each(management_event_ids) WHERE value=OLD.id)",
        "evidence": "EXISTS(SELECT 1 FROM json_each(evidence_ids) WHERE value=OLD.id)",
    }
    for parent, condition in refs.items():
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS pattern_link_retain_{parent} BEFORE DELETE ON {parent}
            WHEN EXISTS(SELECT 1 FROM pattern_entries WHERE {condition})
            BEGIN SELECT RAISE(ABORT,'Record has linked pattern history'); END""")
    for field in ("account_id", "event_date", "session_id", "pattern_id", "follow_up_status", "legacy_journal_entry_id"):
        conn.execute(f"CREATE INDEX IF NOT EXISTS pattern_entries_{field} ON pattern_entries({field})")
    conn.execute("CREATE INDEX IF NOT EXISTS pattern_followups_entry ON pattern_followups(pattern_entry_id,id)")
    # Legacy UI account/symbol edits must not invalidate native context links.
    conn.execute("""CREATE TRIGGER IF NOT EXISTS pattern_trade_context_guard BEFORE UPDATE OF account_id,symbol ON trades
        WHEN (NEW.account_id IS NOT OLD.account_id OR NEW.symbol IS NOT OLD.symbol)
        AND EXISTS(SELECT 1 FROM pattern_entries p WHERE
            EXISTS(SELECT 1 FROM json_each(p.trade_ids) WHERE value=OLD.id) OR p.position_id=OLD.position_id)
        BEGIN SELECT RAISE(ABORT,'Trade has linked pattern context; account/symbol cannot change'); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS pattern_journal_context_guard BEFORE UPDATE OF account_id,session_id,candidate_id,symbol ON journal_entries
        WHEN (NEW.account_id IS NOT OLD.account_id OR NEW.session_id IS NOT OLD.session_id OR NEW.candidate_id IS NOT OLD.candidate_id OR NEW.symbol IS NOT OLD.symbol)
        AND EXISTS(SELECT 1 FROM pattern_entries p WHERE p.parent_journal_entry_id=OLD.id OR p.context_journal_entry_id=OLD.id OR p.legacy_journal_entry_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,'Journal entry has linked pattern context'); END""")
