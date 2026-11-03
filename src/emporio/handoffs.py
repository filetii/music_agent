"""Cases forwarded to the human team, stored in SQLite.

A database (rather than appending to a file) gives every case a unique protocol number even
when several sessions escalate at the same time (the web UI serves many), and lets the team
query open cases. The store's catalog and orders stay read-only CSV.
"""

import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol TEXT UNIQUE,
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    summary TEXT NOT NULL,
    order_id INTEGER,
    customer_contact TEXT
)
"""


def register(db_path: Path, today: date, reason: str, summary: str,
             order_id: int | None = None, customer_contact: str | None = None) -> str:
    """Store a case and return its protocol number (ATD-<date>-<sequence>)."""
    with closing(sqlite3.connect(db_path)) as conn, conn:  # inner `conn`: one committed transaction
        conn.execute(SCHEMA)
        row_id = conn.execute(
            "INSERT INTO handoffs (created_at, reason, summary, order_id, customer_contact) VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), reason, summary, order_id, customer_contact),
        ).lastrowid
        protocol = f"ATD-{today:%Y%m%d}-{row_id:03d}"
        conn.execute("UPDATE handoffs SET protocol = ? WHERE id = ?", (protocol, row_id))
    return protocol
