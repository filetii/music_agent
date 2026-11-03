"""Eval results in SQLite: one row per scenario repetition.

Reports are queries over these raw rows, so every number in them can be traced back (and
recomputed) and models are compared on the same terms.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "results.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    suite TEXT NOT NULL,
    repetitions INTEGER NOT NULL,
    git_commit TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT  -- NULL while running or if interrupted; reports ignore such runs
);
CREATE TABLE IF NOT EXISTS results (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    scenario_id TEXT NOT NULL,
    repetition INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    failures TEXT NOT NULL,  -- JSON list of check failures
    tools TEXT NOT NULL,  -- JSON list of tool names, in call order
    latency_s REAL NOT NULL,
    tokens INTEGER NOT NULL,
    guard_retries INTEGER NOT NULL,
    transcript TEXT NOT NULL,  -- markdown
    PRIMARY KEY (run_id, scenario_id, repetition)
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def start_run(conn: sqlite3.Connection, model: str, suite: str, repetitions: int, git_commit: str | None) -> int:
    with conn:
        return conn.execute(
            "INSERT INTO runs (model, suite, repetitions, git_commit, started_at) VALUES (?, ?, ?, ?, ?)",
            (model, suite, repetitions, git_commit, _now()),
        ).lastrowid


def save_result(conn: sqlite3.Connection, run_id: int, scenario_id: str, repetition: int, failures: list[str],
                tools: list[str], latency_s: float, tokens: int, guard_retries: int, transcript: str) -> None:
    with conn:  # committed per row, so an interrupted run keeps what it measured
        conn.execute(
            "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, scenario_id, repetition, int(not failures), json.dumps(failures, ensure_ascii=False),
             json.dumps(tools), latency_s, tokens, guard_retries, transcript),
        )


def update_grade(conn: sqlite3.Connection, run_id: int, scenario_id: str, repetition: int,
                 failures: list[str], transcript: str) -> None:
    with conn:
        conn.execute(
            "UPDATE results SET passed = ?, failures = ?, transcript = ? "
            "WHERE run_id = ? AND scenario_id = ? AND repetition = ?",
            (int(not failures), json.dumps(failures, ensure_ascii=False), transcript, run_id, scenario_id, repetition),
        )


def finish_run(conn: sqlite3.Connection, run_id: int) -> None:
    with conn:
        conn.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (_now(), run_id))


def latest_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """The most recent finished run of each (model, suite)."""
    return conn.execute(
        "SELECT * FROM runs WHERE id IN "
        "(SELECT MAX(id) FROM runs WHERE finished_at IS NOT NULL GROUP BY model, suite) ORDER BY suite, model"
    ).fetchall()


def run_results(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM results WHERE run_id = ? ORDER BY repetition, rowid", (run_id,)
    ).fetchall()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
