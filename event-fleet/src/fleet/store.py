"""Run persistence for the Conference Prep Fleet.

Every `prep_conference` run is written to a local SQLite file keyed by `run_id`:
the inputs (`PrepRequest`), the assembled output (`ConferenceBriefing`), and a UTC
timestamp. SQLite rather than a hosted DB on purpose -- no network dependency at
demo time, and the file is inspectable with `sqlite3` if a run needs auditing.

Shapes are composed from `fleet.models`, never redefined: a stored row is a
`PrepRequest` plus a `ConferenceBriefing` plus storage metadata.

Errors are never silent. A storage failure logs with its traceback and raises, so
the caller records it as a degradation rather than losing the run quietly.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fleet.models import ConferenceBriefing, PrepRequest

AGENT = "S2/store"
log = logging.getLogger("fleet.store")

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "runs.db"

DEFAULT_CALLER = "local-stdio"
"""Caller identity for the stdio transport.

One stdio server process serves exactly one local client, so a single identity is
the accurate answer today rather than a placeholder. The column exists so the
latest-run rule stays correct if a multi-client transport is ever added.
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    caller     TEXT NOT NULL,
    event_name TEXT NOT NULL,
    intent     TEXT NOT NULL,
    briefing   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_caller_created ON runs (caller, created_at DESC);
"""


@dataclass(frozen=True)
class StoredRun:
    """One persisted run: the frozen models plus how and when it was stored."""

    run_id: str
    caller: str
    request: PrepRequest
    briefing: ConferenceBriefing
    created_at: str


def _connect() -> sqlite3.Connection:
    """Open the run database, creating the file and schema on first use."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _fetch_one(where: str, params: tuple[str, ...]) -> StoredRun | None:
    """Run one lookup and rehydrate the row into models. None means no such run."""
    sql = f"SELECT * FROM runs WHERE {where} ORDER BY created_at DESC LIMIT 1"
    try:
        with _connect() as conn:
            row = conn.execute(sql, params).fetchone()
    except sqlite3.Error as exc:
        log.error(
            "agent=%s step=fetch db=%s where=%s params=%s exc=%r",
            AGENT, DB_PATH, where, params, exc, exc_info=True,
        )
        raise

    if row is None:
        return None
    return StoredRun(
        run_id=row["run_id"],
        caller=row["caller"],
        request=PrepRequest(event_name=row["event_name"], intent=row["intent"]),
        briefing=ConferenceBriefing.model_validate_json(row["briefing"]),
        created_at=row["created_at"],
    )


def save_run(request: PrepRequest, briefing: ConferenceBriefing, caller: str = DEFAULT_CALLER) -> str:
    """Persist one run, keyed by `briefing.run_id`. Returns the run_id.

    Re-running the same run_id replaces the row, so a retry cannot fork a run's history.
    """
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs"
                " (run_id, caller, event_name, intent, briefing, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    briefing.run_id,
                    caller,
                    request.event_name,
                    request.intent,
                    briefing.model_dump_json(),
                    created_at,
                ),
            )
    except sqlite3.Error as exc:
        log.error(
            "agent=%s step=save db=%s run_id=%s event=%r exc=%r",
            AGENT, DB_PATH, briefing.run_id, request.event_name, exc, exc_info=True,
        )
        raise

    log.info(
        "agent=%s step=save run_id=%s caller=%s picks=%d at=%s",
        AGENT, briefing.run_id, caller, len(briefing.picks), created_at,
    )
    return briefing.run_id


def get_run(run_id: str) -> StoredRun | None:
    """Look up one run by its run_id."""
    return _fetch_one("run_id = ?", (run_id,))


def latest_run(caller: str = DEFAULT_CALLER) -> StoredRun | None:
    """The caller's most recent run."""
    return _fetch_one("caller = ?", (caller,))


def resolve_run(run_id: str | None = None, caller: str = DEFAULT_CALLER) -> StoredRun | None:
    """The latest-run rule `submit_eval` depends on.

    With a run_id, that exact run. Without one, the caller's most recent run.
    """
    return get_run(run_id) if run_id else latest_run(caller)
