"""Ingestion registry: schema and initialisation.

The registry is a small DuckDB database holding PIPELINE METADATA only
(analytical tables live in a different file). Its single table,
ingestion_runs, has one row per ingestion attempt. A row is inserted as
'started' and updated once, to 'succeeded' or 'failed', when the attempt
finishes. A skipped ingestion is logged but never stored.

Timestamp convention: every timestamp is UTC, stored as a NAIVE datetime,
because DuckDB's TIMESTAMP type has no timezone. Always create timestamps
with utc_now(), never with datetime.now().
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb

from gridpulse.config import Settings
from gridpulse.utils.logging_setup import get_logger

log = get_logger(__name__)

# The CHECK rules make impossible rows impossible:
#   1. status is one of three values
#   2. 'started' has no completion time; 'succeeded'/'failed' always have one
#   3. 'succeeded' always has a row_count
#   4. 'failed' always explains itself in error_message
# NOTE: DuckDB has no partial unique indexes, so "at most one succeeded run
# per (source_name, sha256)" is NOT enforced here. register_file() enforces it
# in application code.
INGESTION_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id          UUID PRIMARY KEY,

    source_name     VARCHAR NOT NULL,
    file_name       VARCHAR NOT NULL,
    file_path       VARCHAR NOT NULL,

    file_size_bytes BIGINT  NOT NULL,
    sha256          VARCHAR NOT NULL,

    row_count       BIGINT,
    n_date_columns  INTEGER,
    first_date      DATE,
    last_date       DATE,

    status          VARCHAR NOT NULL,

    started_at      TIMESTAMP NOT NULL,
    completed_at    TIMESTAMP,

    error_message   VARCHAR,

    CHECK (status IN ('started', 'succeeded', 'failed')),
    CHECK (
        (status = 'started' AND completed_at IS NULL)
        OR (status IN ('succeeded', 'failed') AND completed_at IS NOT NULL)
    ),
    CHECK (status <> 'succeeded' OR row_count IS NOT NULL),
    CHECK (status <> 'failed' OR error_message IS NOT NULL)
)
"""


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime (see module docstring)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def connect_registry(settings: Settings) -> duckdb.DuckDBPyConnection:
    """Open the registry database with the configured resource limits.

    Creates the parent directory if needed. The caller must close the
    connection, because a DuckDB file allows only one writer at a time.
    """
    db_path = settings.pipeline_metadata_db
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path))
    try:
        memory_limit = settings.duckdb_memory_limit.replace("'", "''")
        con.execute(f"SET memory_limit = '{memory_limit}'")
        con.execute(f"SET threads = {int(settings.duckdb_threads)}")
    except Exception:
        con.close()
        raise
    return con


def initialize_registry(settings: Settings) -> Path:
    """Create the registry database and table if they do not exist.

    Safe to call repeatedly: existing rows are never touched.
    Returns the path of the registry database.
    """
    con = connect_registry(settings)
    try:
        con.execute(INGESTION_RUNS_DDL)
    finally:
        con.close()

    log.info("Registry ready at %s", settings.pipeline_metadata_db)
    return settings.pipeline_metadata_db
