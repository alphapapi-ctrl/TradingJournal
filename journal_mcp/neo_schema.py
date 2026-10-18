"""Additive Neo workflow schema; safe to run on existing journals."""
from __future__ import annotations


TABLES = {
    "trading_sessions": """
        session_key TEXT NOT NULL UNIQUE, account_id INTEGER NOT NULL,
        exchange_date TEXT NOT NULL, local_date TEXT NOT NULL, timezone TEXT NOT NULL,
        label TEXT NOT NULL, journal_entry_id INTEGER, status TEXT NOT NULL DEFAULT 'active',
        review_block INTEGER NOT NULL DEFAULT 0, review_reason TEXT,
        handover TEXT NOT NULL DEFAULT '{}', restart_decision TEXT,
        baseline_version TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1""",
    "candidates": """
        candidate_key TEXT NOT NULL UNIQUE, session_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL, journal_entry_id INTEGER NOT NULL,
        symbol TEXT NOT NULL, direction TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
        plan_version INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1""",
    "candidate_plans": """
        candidate_id INTEGER NOT NULL, version INTEGER NOT NULL, plan TEXT NOT NULL,
        baseline_version TEXT NOT NULL, playbook_id INTEGER, playbook_version TEXT,
        template_id INTEGER, template_version TEXT, playbook_snapshot TEXT, template_snapshot TEXT, reason TEXT NOT NULL,
        UNIQUE(candidate_id,version)""",
    "management_events": """
        event_key TEXT NOT NULL UNIQUE, journal_entry_id INTEGER NOT NULL,
        candidate_id INTEGER, trade_id INTEGER, fill_event_id INTEGER,
        observed_at TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL,
        action_status TEXT NOT NULL, before_stop REAL, after_stop REAL,
        before_target REAL, after_target REAL, rule TEXT, evidence_ids TEXT NOT NULL DEFAULT '[]'""",
    "fill_events": """
        account_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL, trade_id INTEGER NOT NULL,
        source TEXT NOT NULL, source_fill_id TEXT NOT NULL, filled_at TEXT NOT NULL,
        kind TEXT NOT NULL, price REAL NOT NULL, quantity REAL NOT NULL,
        commission REAL NOT NULL DEFAULT 0, other_costs REAL NOT NULL DEFAULT 0,
        swap REAL NOT NULL DEFAULT 0, currency TEXT NOT NULL,
        pnl REAL, corrects_fill_id INTEGER,
        UNIQUE(account_id,source,source_fill_id)""",
    "account_snapshots": """
        account_id INTEGER NOT NULL, balance REAL NOT NULL, equity REAL,
        currency TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL,
        trading_day_timezone TEXT NOT NULL, trading_day_boundary TEXT NOT NULL""",
    "risk_regimes": """
        account_id INTEGER NOT NULL, mode TEXT NOT NULL, risk_pct REAL NOT NULL,
        max_trade_risk REAL, max_combined_risk REAL, max_daily_loss REAL,
        source TEXT NOT NULL, effective_at TEXT NOT NULL, notes TEXT""",
    "instrument_specs": """
        symbol TEXT NOT NULL, version TEXT NOT NULL, quote_currency TEXT NOT NULL,
        contract_multiplier REAL NOT NULL, point_value REAL NOT NULL,
        tick_size REAL NOT NULL, tick_value REAL NOT NULL,
        quantity_step REAL NOT NULL, min_quantity REAL NOT NULL,
        source TEXT NOT NULL, verified_at TEXT NOT NULL, UNIQUE(symbol,version)""",
    "evidence": """
        journal_entry_id INTEGER NOT NULL, candidate_id INTEGER, trade_id INTEGER,
        filename TEXT NOT NULL, mime_type TEXT NOT NULL, sha256 TEXT NOT NULL,
        byte_size INTEGER NOT NULL, stage TEXT NOT NULL, captured_at TEXT NOT NULL,
        symbol TEXT NOT NULL, timeframe TEXT NOT NULL, source TEXT NOT NULL,
        chart_context TEXT, UNIQUE(journal_entry_id,sha256,stage,captured_at)""",
}
IMMUTABLE = set(TABLES) - {"trading_sessions", "candidates"}


def migrate(conn):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _migrate(conn)


def _migrate(conn):
    for name, fields in TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL DEFAULT (datetime('now')), {fields})")
    conn.execute("CREATE TABLE IF NOT EXISTS evidence_blobs (evidence_id INTEGER PRIMARY KEY, content BLOB NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS evidence_access (token_hash TEXT PRIMARY KEY, evidence_id INTEGER NOT NULL, expires_at REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS mcp_requests (request_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL)")
    additions = {
        "journal_entries": {"session_id": "INTEGER", "candidate_id": "INTEGER", "template_id": "INTEGER", "template_version": "TEXT", "baseline_version": "TEXT", "playbook_version": "TEXT"},
        "trades": {"instrument_spec_id": "INTEGER", "sizing_assumptions": "TEXT", "baseline_version": "TEXT", "playbook_version": "TEXT", "template_id": "INTEGER", "template_version": "TEXT"},
        "journal_templates": {"version": "TEXT DEFAULT '1'"},
        "playbooks": {"version": "TEXT DEFAULT '1'"},
    }
    mutable = ("accounts", "trades", "positions", "journal_entries", "journal_templates", "playbooks", "playbook_rules", "playbook_risk_rules", "risk_setups", "replay_sessions", "trading_sessions", "candidates")
    for table in mutable:
        fields = additions.setdefault(table, {})
        fields["revision"] = "INTEGER NOT NULL DEFAULT 1"
    for table, fields in additions.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for field, definition in fields.items():
            if field not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {field} {definition}")
    from journal_mcp.pattern_schema import migrate as migrate_patterns
    migrate_patterns(conn)
    for table in mutable:
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS neo_revision_{table} AFTER UPDATE ON {table}
            WHEN NEW.revision=OLD.revision BEGIN UPDATE {table} SET revision=OLD.revision+1 WHERE id=NEW.id; END""")
    for table in ("playbook_rules", "playbook_risk_rules"):
        for action in ("INSERT", "UPDATE", "DELETE"):
            record = "OLD" if action == "DELETE" else "NEW"
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS neo_playbook_version_{table}_{action} AFTER {action} ON {table}
                BEGIN UPDATE playbooks SET revision=revision+1 WHERE id={record}.playbook_id; END""")
    for table in IMMUTABLE | {"evidence_blobs"}:
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS neo_immutable_{table}_{action} BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT, 'Append-only workflow history; append a correction'); END""")
    for table, fields in {
        "candidates": ("session_id", "account_id", "journal_entry_id"),
        "candidate_plans": ("candidate_id",), "management_events": ("journal_entry_id", "candidate_id", "trade_id"),
        "fill_events": ("candidate_id", "trade_id"), "evidence": ("journal_entry_id", "candidate_id", "trade_id"),
        "account_snapshots": ("account_id",), "risk_regimes": ("account_id",),
    }.items():
        for field in fields:
            conn.execute(f"CREATE INDEX IF NOT EXISTS neo_{table}_{field} ON {table}({field})")
    # These prevent dangling history even when an older UI uses direct SQL deletes.
    for parent, references in {
        "accounts": (("trading_sessions", "account_id"), ("account_snapshots", "account_id"), ("risk_regimes", "account_id")),
        "journal_entries": (("trading_sessions", "journal_entry_id"), ("candidates", "journal_entry_id"), ("evidence", "journal_entry_id"), ("management_events", "journal_entry_id")),
        "trades": (("fill_events", "trade_id"), ("evidence", "trade_id"), ("management_events", "trade_id")),
    }.items():
        condition = " OR ".join(f"EXISTS(SELECT 1 FROM {child} WHERE {field}=OLD.id)" for child, field in references)
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS neo_retain_{parent} BEFORE DELETE ON {parent}
            WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Record has durable Neo history and cannot be deleted'); END""")
