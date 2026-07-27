"""
Database layer for Trading Journal - SQLite via SQLAlchemy
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "journal.db"

def get_connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = get_connection()
    c = conn.cursor()

    # Accounts table
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            broker TEXT NOT NULL,
            account_number TEXT,
            currency TEXT DEFAULT 'AUD',
            initial_balance REAL DEFAULT 0,
            is_default INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT
        )
    """)

    # Trades table
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            broker_trade_id TEXT,
            broker TEXT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,  -- LONG / SHORT
            entry_price REAL,
            exit_price REAL,
            entry_time TEXT,
            exit_time TEXT,
            quantity REAL,
            pnl REAL,
            commission REAL DEFAULT 0,
            swap REAL DEFAULT 0,
            raw_data TEXT,
            position_id INTEGER,
            playbook_id INTEGER,
            playbook_rules_met TEXT,
            risk_score REAL,
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        )
    """)

    # Positions table (merged trades)
    c.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            open_time TEXT,
            close_time TEXT,
            avg_entry_price REAL,
            avg_exit_price REAL,
            total_quantity REAL,
            total_pnl REAL,
            playbook_id INTEGER,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Playbooks table
    c.execute("""
        CREATE TABLE IF NOT EXISTS playbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            symbol_filter TEXT,  -- CSV of symbols or '*'
            direction TEXT DEFAULT 'BOTH',  -- LONG / SHORT / BOTH
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Playbook rules
    c.execute("""
        CREATE TABLE IF NOT EXISTS playbook_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playbook_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            rule_type TEXT NOT NULL,  -- required / optional / bonus
            weight REAL DEFAULT 1.0,
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY (playbook_id) REFERENCES playbooks(id)
        )
    """)

    # Risk thresholds for playbooks
    c.execute("""
        CREATE TABLE IF NOT EXISTS playbook_risk_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playbook_id INTEGER NOT NULL,
            min_required_pct REAL DEFAULT 100,  -- % of required rules needed
            min_optional_pct REAL DEFAULT 0,    -- % of optional rules needed
            trigger_rule_id INTEGER DEFAULT NULL, -- specific rule that must be met (NULL = use pct logic)
            trigger_rule_must_be INTEGER DEFAULT 1, -- 1=must be met, 0=must NOT be met
            risk_level TEXT DEFAULT 'normal',   -- normal / reduced / no_trade
            risk_multiplier REAL DEFAULT 1.0,   -- multiply position size by this
            warning_message TEXT,
            FOREIGN KEY (playbook_id) REFERENCES playbooks(id),
            FOREIGN KEY (trigger_rule_id) REFERENCES playbook_rules(id)
        )
    """)

    # Journal entries
    c.execute("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_type TEXT NOT NULL,  -- daily / weekly / trade
            entry_date TEXT NOT NULL,
            trade_id INTEGER,
            position_id INTEGER,
            playbook_id INTEGER,       -- for pre-trade entries without a trade yet
            stage TEXT DEFAULT 'post', -- pre / post (for trade entries)
            -- Pre-trade fields
            pre_analysis TEXT,         -- market read, setup rationale
            pre_plan TEXT,             -- entry plan, levels, invalidation
            pre_playbook_notes TEXT,   -- which playbook rules are present
            pre_psychology TEXT,       -- mindset before trade
            pre_risk_notes TEXT,       -- planned risk, size rationale
            -- Post-trade fields
            analysis TEXT,
            execution TEXT,
            psychology TEXT,
            lessons TEXT,
            grade TEXT,
            mood INTEGER,
            custom_fields TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Journal templates
    c.execute("""
        CREATE TABLE IF NOT EXISTS journal_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            template_type TEXT NOT NULL,  -- daily / weekly / trade
            analysis_template TEXT,
            execution_template TEXT,
            psychology_template TEXT,
            custom_fields TEXT,  -- JSON array of field definitions
            is_default INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # App settings (key-value store)
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Import history
    c.execute("""
        CREATE TABLE IF NOT EXISTS import_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL,
            filename TEXT,
            trades_imported INTEGER DEFAULT 0,
            imported_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'success'
        )
    """)

    # Saved risk-calculator setups (revisit when capital frees up)
    c.execute("""
        CREATE TABLE IF NOT EXISTS risk_setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT DEFAULT 'LONG',
            entry_price REAL,
            stop_price REAL,
            risk_pct REAL,
            balance REAL,
            account_id INTEGER,
            shares REAL,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    _run_migrations(conn)
    conn.close()
    _seed_default_templates()


def _run_migrations(conn):
    c = conn.cursor()
    migrations = [
        ("trades",              "account_id",           "INTEGER"),
        ("trades",              "trade_type",           "TEXT"),
        ("journal_entries",     "stage",                "TEXT DEFAULT 'post'"),
        ("journal_entries",     "playbook_id",          "INTEGER"),
        ("journal_entries",     "pre_analysis",         "TEXT"),
        ("journal_entries",     "pre_plan",             "TEXT"),
        ("journal_entries",     "pre_playbook_notes",   "TEXT"),
        ("journal_entries",     "pre_psychology",       "TEXT"),
        ("journal_entries",     "pre_risk_notes",       "TEXT"),
        ("playbook_risk_rules", "trigger_rule_id",      "INTEGER DEFAULT NULL"),
        ("playbook_risk_rules", "trigger_rule_must_be", "INTEGER DEFAULT 1"),
        ("accounts",            "ftp_folder",                 "TEXT"),
        ("accounts",            "account_type",               "TEXT DEFAULT 'Personal'"),
        ("accounts",            "prop_profit_target_pct",     "REAL"),
        ("accounts",            "prop_max_loss_pct",          "REAL"),
        ("accounts",            "prop_daily_loss_pct",        "REAL"),
        ("accounts",            "prop_personal_daily_loss_pct", "REAL"),
        ("journal_entries",     "trade_category",       "INTEGER"),  # Menaker 1-4
        ("journal_entries",     "account_id",           "INTEGER"),
        ("playbook_rules",      "rule_group",           "TEXT"),  # same group = OR (any one satisfies)
        ("journal_entries",     "pre_body_state",       "TEXT"),     # somatic check-in
        ("journal_entries",     "pre_accuracy_check",   "TEXT"),     # accurate vs making money
        ("journal_entries",     "pre_risk_type",        "TEXT"),     # smart vs sloppy risk
    ]
    for table, col, col_def in migrations:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
        except Exception:
            pass
    conn.commit()


def _seed_default_templates():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM journal_templates")
    if c.fetchone()[0] > 0:
        conn.close()
        return

    templates = [
        ("Default Trade Review", "trade",
         "## Market Context\n\n## Setup Criteria\n\n## Entry Rationale\n\n## Target & Stop Logic\n",
         "## Entry Execution\n- Spread at entry:\n- Slippage:\n- Order type used:\n\n## Exit Execution\n- Exit trigger:\n- Was plan followed?\n",
         "## Pre-trade mindset:\n\n## During trade emotions:\n\n## Post-trade reflection:\n\n## Discipline score (1-10):",
         None, 1),
        ("Daily Review", "daily",
         "## Pre-Market Analysis\n\n## Key Levels Today\n\n## Market Bias\n",
         "## Trades Taken\n\n## Missed Opportunities\n\n## Execution Quality\n",
         "## Mindset Today\n\n## Focus Level (1-10):\n\n## Stress Events:\n",
         None, 1),
        ("Weekly Review", "weekly",
         "## Weekly Bias & Theme\n\n## Economic Events Impact\n\n## Best Setup of the Week\n",
         "## Win/Loss Breakdown\n\n## Biggest Mistakes\n\n## Process Compliance\n",
         "## Overall Week Mindset\n\n## Lessons Learned\n\n## Goals for Next Week\n",
         None, 1),
    ]
    c.executemany("""
        INSERT INTO journal_templates (name, template_type, analysis_template, execution_template, psychology_template, custom_fields, is_default)
        VALUES (?,?,?,?,?,?,?)
    """, templates)
    conn.commit()
    conn.close()


# ─── CRUD helpers ────────────────────────────────────────────────────────────

def fetch_all(query, params=()):
    conn = get_connection()
    c = conn.cursor()
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def fetch_one(query, params=()):
    conn = get_connection()
    c = conn.cursor()
    c.execute(query, params)
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def execute(query, params=()):
    conn = get_connection()
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    last_id = c.lastrowid
    conn.close()
    return last_id
