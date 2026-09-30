from __future__ import annotations

import sqlite3
from pathlib import Path

REQUIRED_TABLES = {
    "bom_rows",
    "drawings",
    "drawing_claims",
    "drawing_bom_links",
    "interfaces",
    "reconstructions",
    "corrections",
    "correction_events",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bom_rows (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  name_norm TEXT NOT NULL,
  part_family TEXT,
  type TEXT,
  design_state TEXT,
  order_state TEXT,
  vv_state TEXT,
  notes_raw TEXT,
  design_intent_raw TEXT,
  material_raw TEXT,
  product_name TEXT,
  supplier_order TEXT,
  interface_with_raw TEXT,
  link TEXT,
  supplier_raw TEXT,
  datasheet_raw TEXT,
  amount_raw TEXT,
  amount_parsed REAL,
  cost_raw TEXT,
  cost_dkk REAL,
  total_cost_raw TEXT,
  total_cost_dkk REAL,
  interface_count_raw TEXT,
  source_file TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  file_line INTEGER
);

CREATE TABLE IF NOT EXISTS drawings (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  source_filename TEXT NOT NULL,
  subsystem TEXT NOT NULL,
  pages INTEGER NOT NULL,
  distribution TEXT NOT NULL,
  role TEXT,
  title_extracted TEXT,
  title_confidence TEXT,
  material_extracted TEXT,
  material_confidence TEXT,
  scale TEXT,
  sheet_label TEXT,
  weight_g REAL,
  weight_note TEXT,
  drawn_date TEXT,
  drawn_by TEXT,
  extract_method TEXT
);

CREATE TABLE IF NOT EXISTS drawing_claims (
  id INTEGER PRIMARY KEY,
  drawing_id TEXT NOT NULL REFERENCES drawings(id),
  page INTEGER NOT NULL,
  field TEXT NOT NULL,
  value_raw TEXT NOT NULL,
  value_norm TEXT,
  unit TEXT,
  region TEXT,
  confidence TEXT NOT NULL,
  method TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drawing_bom_links (
  id INTEGER PRIMARY KEY,
  drawing_id TEXT NOT NULL REFERENCES drawings(id),
  bom_row_id INTEGER REFERENCES bom_rows(id),
  method TEXT NOT NULL,
  evidence TEXT NOT NULL,
  score REAL NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interfaces (
  id INTEGER PRIMARY KEY,
  from_bom_row_id INTEGER NOT NULL REFERENCES bom_rows(id),
  to_name_raw TEXT NOT NULL,
  to_bom_row_id INTEGER REFERENCES bom_rows(id),
  match_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconstructions (
  drawing_id TEXT PRIMARY KEY REFERENCES drawings(id),
  label TEXT NOT NULL,
  param_path TEXT NOT NULL,
  mesh_path TEXT NOT NULL,
  disclaimer TEXT NOT NULL,
  assumptions TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY,
  proposed_at TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  field TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT NOT NULL,
  reason TEXT,
  check_notes TEXT,
  status TEXT NOT NULL,
  reviewed_at TEXT,
  reviewer TEXT,
  reviewer_note TEXT,
  target_fact_ref TEXT,
  chat_turn_id TEXT,
  checks TEXT
);

CREATE TABLE IF NOT EXISTS correction_events (
  id INTEGER PRIMARY KEY,
  correction_id INTEGER NOT NULL REFERENCES corrections(id),
  event TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _ensure_columns(conn)
    conn.commit()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    bom_cols = {row[1] for row in conn.execute("PRAGMA table_info(bom_rows)")}
    if "file_line" not in bom_cols:
        conn.execute("ALTER TABLE bom_rows ADD COLUMN file_line INTEGER")
    correction_cols = {row[1] for row in conn.execute("PRAGMA table_info(corrections)")}
    for name in ("reviewer_note", "target_fact_ref", "chat_turn_id", "checks"):
        if name not in correction_cols:
            conn.execute(f"ALTER TABLE corrections ADD COLUMN {name} TEXT")
