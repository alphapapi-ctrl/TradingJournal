"""
Database layer for Trading Journal — SQLite (sqlite3).
"""
import sqlite3
import os
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
_PRIMARY_DB_PATH = _DATA_DIR / "journal.db"
_LEGACY_DB_PATH = _DATA_DIR / "trading_journal.db"


def _resolve_db_path() -> Path:
    # Environment override for deployments where the DB is kept outside the app folder.
    override = os.getenv("TRADING_JOURNAL_DB_PATH") or os.getenv("TJ_DB_PATH")
    if override:
        return Path(override).expanduser()

    # Prefer the current primary DB name, but auto-fallback to the legacy name
    # if the project was previously running from that path.
    if _PRIMARY_DB_PATH.exists():
        return _PRIMARY_DB_PATH
    if _LEGACY_DB_PATH.exists():
        return _LEGACY_DB_PATH
    return _PRIMARY_DB_PATH


DB_PATH = _resolve_db_path()

def _data_dir():
    _DATA_DIR.mkdir(exist_ok=True)
    return _DATA_DIR

def get_connection():
    _data_dir()
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
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

    # Per-day strategy features for the DAX Reference scanner (built from
    # School Run App 1-min parquet caches; see utils/day_index.py)
    c.execute("""
        CREATE TABLE IF NOT EXISTS day_features (
            date TEXT NOT NULL,
            instrument TEXT NOT NULL DEFAULT 'DEUIDXEUR',
            session_open REAL, session_close REAL,
            session_high REAL, session_low REAL,
            prior_high REAL, prior_low REAL, prior_close REAL,
            overnight_high REAL, overnight_low REAL,
            gap_pts REAL, gap_pct REAL, gap_dir TEXT,
            gap_closed INTEGER, gap_close_time TEXT,
            open_vs_prior TEXT, open_vs_onr TEXT,
            b1_o REAL, b1_h REAL, b1_l REAL, b1_c REAL, b1_type TEXT,
            b2_o REAL, b2_h REAL, b2_l REAL, b2_c REAL, b2_type TEXT,
            b2_rel_b1 TEXT,
            sr_long_entry REAL, sr_long_stop REAL,
            sr_short_entry REAL, sr_short_stop REAL,
            news_flags TEXT,
            computed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (date, instrument)
        )
    """)

    # Saved replay-trainer sessions (resume a day mid-replay)
    c.execute("""
        CREATE TABLE IF NOT EXISTS replay_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            instrument TEXT NOT NULL,
            day TEXT NOT NULL,
            cursor INTEGER DEFAULT 0,
            sub_step INTEGER DEFAULT 0,
            speed REAL DEFAULT 10,
            pending_orders TEXT,          -- JSON list of working orders
            status TEXT DEFAULT 'active', -- active / archived
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
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
        ("trades",              "stop_price",           "REAL"),     # replay trainer
        ("trades",              "take_profit",          "REAL"),
        ("trades",              "planned_risk",         "REAL"),     # $ at risk at entry
        ("trades",              "r_multiple",           "REAL"),
        ("trades",              "is_replay",            "INTEGER DEFAULT 0"),
        ("trades",              "replay_day",           "TEXT"),     # historical day being replayed
        ("replay_sessions",     "account_id",           "INTEGER"),  # per-session demo account
        ("journal_entries",     "symbol",               "TEXT"),     # ticker for pre-trade plans
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
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(query, params)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except sqlite3.OperationalError as exc:
        if "no such table:" in str(exc).lower():
            init_db()
            conn = get_connection()
            c = conn.cursor()
            c.execute(query, params)
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            return rows
        raise

def fetch_one(query, params=()):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(query, params)
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None
    except sqlite3.OperationalError as exc:
        if "no such table:" in str(exc).lower():
            init_db()
            conn = get_connection()
            c = conn.cursor()
            c.execute(query, params)
            row = c.fetchone()
            conn.close()
            return dict(row) if row else None
        raise

def execute(query, params=()):
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(query, params)
        conn.commit()
        last_id = c.lastrowid
        conn.close()
        return last_id
    except sqlite3.OperationalError as exc:
        if "no such table:" in str(exc).lower():
            init_db()
            conn = get_connection()
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            last_id = c.lastrowid
            conn.close()
            return last_id
        raise
