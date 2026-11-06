"""sqlite schema and connection. every table carries timestamps, every action goes to events."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  source_file TEXT NOT NULL,
  started_at TEXT NOT NULL,
  rows_read INTEGER DEFAULT 0,
  leads_added INTEGER DEFAULT 0,
  companies_added INTEGER DEFAULT 0,
  rejected_json TEXT DEFAULT '{}',
  unmapped_columns TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL UNIQUE,
  name TEXT,
  industry TEXT,
  headcount INTEGER,
  country TEXT,
  linkedin_url TEXT,
  source TEXT NOT NULL,
  source_file TEXT,
  import_id INTEGER REFERENCES imports(id),
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  raw_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY,
  company_id INTEGER REFERENCES companies(id),
  email TEXT NOT NULL UNIQUE,
  email_domain TEXT NOT NULL,
  first_name TEXT,
  last_name TEXT,
  title TEXT,
  linkedin_url TEXT,
  country TEXT,
  email_status_source TEXT,          -- status as given by the export, e.g. Clay
  verify_status TEXT,                -- set in phase 2
  score INTEGER,
  score_reason TEXT,
  tier TEXT,                         -- A, B, C, dnc
  status TEXT NOT NULL DEFAULT 'new',-- new, queued, in_sequence, replied, stopped
  source TEXT NOT NULL,
  source_file TEXT,
  import_id INTEGER REFERENCES imports(id),
  imported_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  raw_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY,
  lead_id INTEGER REFERENCES leads(id),
  company_id INTEGER REFERENCES companies(id),
  type TEXT NOT NULL,
  value TEXT NOT NULL,
  source TEXT NOT NULL,
  source_field TEXT NOT NULL,
  observed_at TEXT,
  detected_at TEXT NOT NULL,
  expires_at TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  step INTEGER NOT NULL,
  variant TEXT,
  subject TEXT,
  body TEXT,
  signal_ids TEXT DEFAULT '[]',
  qa_verdict TEXT,
  qa_reasons TEXT DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS domains (
  id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL UNIQUE,
  spf_ok INTEGER, dkim_ok INTEGER, dmarc_ok INTEGER, mx_ok INTEGER,
  checked_at TEXT,
  paused INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mailboxes (
  id INTEGER PRIMARY KEY,
  address TEXT NOT NULL UNIQUE,
  domain_id INTEGER REFERENCES domains(id),
  warmup_started_at TEXT,
  daily_cap INTEGER NOT NULL DEFAULT 10,
  paused INTEGER NOT NULL DEFAULT 0,
  pause_reason TEXT
);

CREATE TABLE IF NOT EXISTS sends (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  mailbox_id INTEGER REFERENCES mailboxes(id),
  scheduled_at TEXT,
  sent_at TEXT,
  mode TEXT NOT NULL DEFAULT 'dry_run',  -- dry_run or live
  status TEXT NOT NULL DEFAULT 'scheduled',
  provider_id TEXT
);

CREATE TABLE IF NOT EXISTS replies (
  id INTEGER PRIMARY KEY,
  lead_id INTEGER REFERENCES leads(id),
  send_id INTEGER REFERENCES sends(id),
  received_at TEXT NOT NULL,
  from_address TEXT,
  subject TEXT,
  body TEXT,
  category TEXT,
  confidence REAL,
  draft TEXT,
  handled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS suppression (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('email', 'domain')),
  value TEXT NOT NULL,
  reason TEXT NOT NULL,
  added_at TEXT NOT NULL,
  UNIQUE (type, value)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  action TEXT NOT NULL,
  entity TEXT,
  entity_id TEXT,
  reason TEXT,
  data_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS field_values (
  id INTEGER PRIMARY KEY,
  entity TEXT NOT NULL CHECK (entity IN ('lead', 'company')),
  entity_id INTEGER NOT NULL,
  field TEXT NOT NULL,
  value TEXT,
  source TEXT NOT NULL,          -- e.g. explorium, clay_csv
  source_ref TEXT,               -- endpoint + correlation id, or file:line
  observed_at TEXT,              -- when the fact happened, if the source says
  fetched_at TEXT NOT NULL       -- when we pulled it
);

CREATE TABLE IF NOT EXISTS api_calls (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  mode TEXT NOT NULL,            -- free or paid
  records INTEGER,
  credits INTEGER,               -- as reported by the provider, null if not reported
  correlation_id TEXT,
  run_id TEXT
);

CREATE TABLE IF NOT EXISTS reply_exports (
  id INTEGER PRIMARY KEY,
  export_id TEXT NOT NULL,
  reply_id INTEGER NOT NULL REFERENCES replies(id),
  ctx_json TEXT NOT NULL,         -- what was handed out, checked again on import
  created_at TEXT NOT NULL,
  imported_at TEXT,
  UNIQUE (export_id, reply_id)
);

CREATE TABLE IF NOT EXISTS linkedin_log (
  id INTEGER PRIMARY KEY,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  kind TEXT NOT NULL,            -- connect or followup
  note TEXT NOT NULL,
  list_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'listed',   -- listed, sent, accepted, replied, skipped
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  text TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT 'abdul',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summary_snapshots (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sequence_state (
  lead_id INTEGER PRIMARY KEY REFERENCES leads(id),
  company_id INTEGER,
  mailbox_id INTEGER REFERENCES mailboxes(id),
  thread_subject TEXT,
  first_message_id TEXT,          -- Message-ID of email 1, for In-Reply-To on same thread follow ups
  step_done INTEGER NOT NULL DEFAULT 0,
  next_step INTEGER,
  next_due TEXT,                  -- date (YYYY-MM-DD)
  status TEXT NOT NULL DEFAULT 'active',   -- active, stopped, completed
  stop_reason TEXT,
  started_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS email_exports (
  id INTEGER PRIMARY KEY,
  export_id TEXT NOT NULL,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  variant TEXT NOT NULL,
  needs TEXT NOT NULL,            -- json list: signal_email and/or fallback_email
  ctx_json TEXT NOT NULL,         -- the exact facts given out, checked again on import
  created_at TEXT NOT NULL,
  imported_at TEXT,
  UNIQUE (export_id, lead_id)
);

CREATE TABLE IF NOT EXISTS signal_picks (
  id INTEGER PRIMARY KEY,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  signal_id INTEGER NOT NULL REFERENCES signals(id),
  rank INTEGER NOT NULL,
  signal_class TEXT NOT NULL,
  points INTEGER,
  age_days INTEGER,
  saw_line TEXT NOT NULL,
  matters_line TEXT NOT NULL,
  short_line TEXT,
  picked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_field_values ON field_values(entity, entity_id);
CREATE INDEX IF NOT EXISTS idx_leads_company ON leads(company_id);
CREATE INDEX IF NOT EXISTS idx_events_action ON events(action);
CREATE INDEX IF NOT EXISTS idx_signals_lead ON signals(lead_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# columns added after the first schema. connect() adds them to older databases.
MIGRATIONS = {
    "leads": {"verify_reason": "TEXT", "verified_at": "TEXT", "niche": "TEXT", "scored_at": "TEXT",
              "fallback": "INTEGER", "booked_at": "TEXT", "follow_up_at": "TEXT", "referred_by_lead_id": "INTEGER"},
    "replies": {"message_id": "TEXT", "in_reply_to": "TEXT", "matched_by": "TEXT", "status": "TEXT",
                "reason": "TEXT", "follow_up_at": "TEXT", "referral_json": "TEXT", "export_id": "TEXT",
                "draft_status": "TEXT", "reviewed_at": "TEXT", "review_note": "TEXT", "to_address": "TEXT",
                "check_reasons": "TEXT", "classified_by": "TEXT"},
    "sequence_state": {"paused_until": "TEXT"},
    "sends": {"lead_id": "INTEGER", "step": "INTEGER", "message_id_header": "TEXT", "outcome": "TEXT",
              "outcome_at": "TEXT", "headers_json": "TEXT"},
    "mailboxes": {"live_from": "TEXT"},
    "messages": {"status": "TEXT", "model": "TEXT", "input_tokens": "INTEGER", "output_tokens": "INTEGER",
                 "cost_usd": "REAL", "uses_fallback": "INTEGER", "facts_json": "TEXT", "signal_class": "TEXT",
                 "attempts": "INTEGER", "review": "TEXT", "reviewed_at": "TEXT", "review_note": "TEXT",
                 "writer_mode": "TEXT"},
    "signals": {"data_json": "TEXT", "signal_class": "TEXT", "expires_at": "TEXT", "expired_at": "TEXT"},
}


def _migrate(conn) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def log_event(conn, action: str, entity: str | None = None, entity_id=None,
              reason: str | None = None, **data) -> None:
    conn.execute(
        "INSERT INTO events (ts, action, entity, entity_id, reason, data_json) VALUES (?,?,?,?,?,?)",
        (now(), action, entity, None if entity_id is None else str(entity_id), reason,
         json.dumps(data, default=str)),
    )
