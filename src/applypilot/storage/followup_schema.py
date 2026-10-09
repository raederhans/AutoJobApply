"""Versioned recruiting records. These tables never grant submission authority."""

import sqlite3


def exists(conn: sqlite3.Connection | None, table: str) -> bool:
    return conn is not None and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def ensure_event_schema(conn: sqlite3.Connection) -> None:
    """Preserve old observations and IDs while allowing multiple facts per message."""
    if exists(conn, "followup_events"):
        columns = {row[1] for row in conn.execute("PRAGMA table_info(followup_events)")}
        if "fact_key" not in columns:
            extras = [row[0] for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name='followup_events' "
                "AND type IN ('index','trigger') AND sql IS NOT NULL"
            )]
            conn.execute("""CREATE TABLE followup_events_upgrade (
                event_id TEXT PRIMARY KEY, provider TEXT NOT NULL, message_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
                job_url TEXT, resolved_at TEXT, created_at TEXT NOT NULL,
                fact_key TEXT NOT NULL DEFAULT '', application_id TEXT,
                UNIQUE(provider, message_id, fact_key)
            )""")
            conn.execute("""INSERT INTO followup_events_upgrade
                (event_id,provider,message_id,occurred_at,event_type,payload,job_url,resolved_at,created_at)
                SELECT event_id,provider,message_id,occurred_at,event_type,payload,job_url,resolved_at,created_at
                FROM followup_events""")
            conn.execute("DROP TABLE followup_events")
            conn.execute("ALTER TABLE followup_events_upgrade RENAME TO followup_events")
            for statement in extras:
                conn.execute(statement)
    else:
        conn.execute("""CREATE TABLE followup_events (
            event_id TEXT PRIMARY KEY, provider TEXT NOT NULL, message_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
            job_url TEXT, resolved_at TEXT, created_at TEXT NOT NULL,
            fact_key TEXT NOT NULL DEFAULT '', application_id TEXT,
            UNIQUE(provider, message_id, fact_key)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_followup_application ON followup_events(application_id, occurred_at)")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS followup_applications (
        application_id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
        job_url TEXT, company TEXT NOT NULL, title TEXT NOT NULL,
        submitted_at TEXT, date_precision TEXT NOT NULL DEFAULT 'unknown',
        submission_basis TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_followup_job ON followup_applications(job_url)")
    conn.execute("""CREATE TABLE IF NOT EXISTS followup_application_refs (
        application_id TEXT NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
        PRIMARY KEY(source_type, source_id),
        FOREIGN KEY(application_id) REFERENCES followup_applications(application_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS followup_sync_runs (
        provider TEXT NOT NULL, run_id TEXT NOT NULL, status TEXT NOT NULL,
        attempted_at TEXT NOT NULL, cutoff TEXT, complete INTEGER NOT NULL,
        imported INTEGER NOT NULL, duplicates INTEGER NOT NULL, pending INTEGER NOT NULL,
        PRIMARY KEY(provider, run_id)
    )""")
    # Fresh databases keep the original lazy followup event/action contract.
    if exists(conn, "followup_events"):
        ensure_event_schema(conn)
