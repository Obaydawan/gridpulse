"""Ingestion registry: schema, file hashing and file registration.

The registry is a small DuckDB database holding PIPELINE METADATA only
(analytical tables live in a different file). Its single table,
ingestion_runs, has one row per ingestion attempt. A row is inserted as
'started' and updated once, to 'succeeded' or 'failed', when the attempt
finishes. A skipped ingestion is logged and returned, but never stored.

Timestamp convention: every timestamp is UTC, stored as a NAIVE datetime,
because DuckDB's TIMESTAMP type has no timezone. Always create timestamps
with utc_now(), never with datetime.now().

Known limitation: two processes registering at the same moment could both
pass the "already succeeded?" check. This project runs one process at a time.
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import duckdb

from gridpulse.config import Settings
from gridpulse.utils.logging_setup import get_logger

log = get_logger(__name__)

CHUNK_SIZE = 1024 * 1024  # hash files 1 MiB at a time, never all at once
MAX_ERROR_LENGTH = 2000

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


@dataclass(frozen=True)
class FileMetadata:
    """Optional facts a validator can report about a file."""

    n_date_columns: int | None = None
    first_date: date | None = None
    last_date: date | None = None


# A validator checks one file and returns its metadata. It must RAISE an
# exception if the file is not acceptable.
Validator = Callable[[Path], FileMetadata]


@dataclass(frozen=True)
class IngestionResult:
    """What register_file() reports back.

    For a skipped file, run_id is the id of the EXISTING succeeded run, so
    later stages always know which run supplied the data.
    """

    run_id: uuid.UUID
    outcome: Literal["succeeded", "skipped"]
    sha256: str
    row_count: int | None


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime (see module docstring)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _apply_limits(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Apply the configured memory and thread limits to a connection."""
    memory_limit = settings.duckdb_memory_limit.replace("'", "''")
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"SET threads = {int(settings.duckdb_threads)}")


def connect_registry(settings: Settings) -> duckdb.DuckDBPyConnection:
    """Open the registry database with the configured resource limits.

    Creates the parent directory if needed. The caller must close the
    connection, because a DuckDB file allows only one writer at a time.
    """
    db_path = settings.pipeline_metadata_db
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path))
    try:
        _apply_limits(con, settings)
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


def hash_file(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    """Return the SHA-256 of a file as a hex string, reading it in chunks.

    Reading in chunks keeps memory use tiny, even for very large files.
    """
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _stored_path(path: Path, project_root: Path) -> str:
    """Path as stored in the registry: relative to the project root if inside it."""
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        log.info("File is outside the project root, storing its absolute path: %s", path)
        return str(path.resolve())


def _count_csv_rows(path: Path, settings: Settings) -> int:
    """Count data rows (excluding the header) with DuckDB.

    all_varchar=true reads every cell as text, so type guessing cannot fail,
    and quoted fields containing line breaks are counted correctly.
    """
    con = duckdb.connect()  # in-memory
    try:
        _apply_limits(con, settings)
        (count,) = con.execute(
            "SELECT count(*) FROM read_csv(?, header = true, all_varchar = true)",
            [str(path)],
        ).fetchone()
    finally:
        con.close()
    return int(count)


def _begin_attempt(
    settings: Settings,
    source_name: str,
    path: Path,
    stored_path: str,
    size: int,
    sha256: str,
) -> uuid.UUID | IngestionResult:
    """Transaction 1: skip if already ingested, otherwise insert a 'started' row.

    Returns an IngestionResult (outcome 'skipped') or the new run's id.
    Only a SUCCEEDED run blocks a new attempt; failed and stale 'started'
    rows never do.
    """
    con = connect_registry(settings)
    try:
        con.begin()
        try:
            existing = con.execute(
                "SELECT run_id, row_count FROM ingestion_runs "
                "WHERE source_name = ? AND sha256 = ? AND status = 'succeeded' "
                "ORDER BY completed_at DESC LIMIT 1",
                [source_name, sha256],
            ).fetchone()
            if existing is not None:
                con.rollback()
                return IngestionResult(existing[0], "skipped", sha256, existing[1])

            (changed,) = con.execute(
                "SELECT count(DISTINCT sha256) FROM ingestion_runs "
                "WHERE source_name = ? AND file_path = ? "
                "AND status = 'succeeded' AND sha256 <> ?",
                [source_name, stored_path, sha256],
            ).fetchone()
            if changed:
                log.warning(
                    "%s was ingested before with DIFFERENT content "
                    "(raw files are meant to be immutable)",
                    stored_path,
                )

            run_id = uuid.uuid4()
            con.execute(
                "INSERT INTO ingestion_runs (run_id, source_name, file_name, "
                "file_path, file_size_bytes, sha256, status, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'started', ?)",
                [run_id, source_name, path.name, stored_path, size, sha256, utc_now()],
            )
            con.commit()
            return run_id
        except Exception:
            with contextlib.suppress(Exception):
                con.rollback()
            raise
    finally:
        con.close()


def _finish_attempt(
    settings: Settings, run_id: uuid.UUID, metadata: FileMetadata, row_count: int
) -> None:
    """Transaction 3 (success): mark the attempt 'succeeded'."""
    con = connect_registry(settings)
    try:
        con.begin()
        try:
            (updated,) = con.execute(
                "UPDATE ingestion_runs SET status = 'succeeded', row_count = ?, "
                "n_date_columns = ?, first_date = ?, last_date = ?, completed_at = ? "
                "WHERE run_id = ? AND status = 'started'",
                [
                    row_count,
                    metadata.n_date_columns,
                    metadata.first_date,
                    metadata.last_date,
                    utc_now(),
                    run_id,
                ],
            ).fetchone()
            if updated != 1:
                raise RuntimeError(f"expected to update 1 run row, updated {updated}")
            con.commit()
        except Exception:
            with contextlib.suppress(Exception):
                con.rollback()
            raise
    finally:
        con.close()


def _record_failure(settings: Settings, run_id: uuid.UUID, exc: Exception) -> None:
    """Transaction 3 (failure): mark the attempt 'failed'.

    Never raises: if the failure cannot be recorded, that is logged and the
    caller still re-raises the ORIGINAL error. The row then stays 'started'.
    """
    message = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
    try:
        con = connect_registry(settings)
        try:
            con.execute(
                "UPDATE ingestion_runs SET status = 'failed', error_message = ?, "
                "completed_at = ? WHERE run_id = ? AND status = 'started'",
                [message, utc_now(), run_id],
            )
        finally:
            con.close()
    except Exception:
        log.exception("Could not record the failure of run %s in the registry", run_id)


def register_file(
    settings: Settings,
    source_name: str,
    file_path: Path | str,
    validator: Validator,
) -> IngestionResult:
    """Register one raw file in the ingestion registry.

    Flow (three short transactions, never one long one):
      1. hash the file; skip if this source + hash already succeeded;
         otherwise insert a 'started' row and COMMIT.
      2. run the validator and count rows, OUTSIDE any transaction.
      3. mark the row 'succeeded' (or 'failed') and COMMIT.

    On failure the error is recorded and the ORIGINAL exception is re-raised.
    KeyboardInterrupt is deliberately not caught: an interrupted run stays
    'started', which is how an interrupted run should look in the registry.
    A missing file raises FileNotFoundError and creates no row.
    """
    if not source_name or not source_name.strip():
        raise ValueError("source_name must not be empty")

    path = Path(file_path)
    if not path.is_absolute():
        path = settings.project_root / path
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    initialize_registry(settings)

    size = path.stat().st_size
    sha256 = hash_file(path)
    stored_path = _stored_path(path, settings.project_root)
    log.info(
        "Registering %s for source %r (%d bytes, sha256 %s)",
        stored_path,
        source_name,
        size,
        sha256,
    )

    began = _begin_attempt(settings, source_name, path, stored_path, size, sha256)
    if isinstance(began, IngestionResult):
        log.info(
            "Skipping %s: already ingested successfully (run %s)",
            stored_path,
            began.run_id,
        )
        return began
    run_id = began

    try:
        metadata = validator(path)
        row_count = _count_csv_rows(path, settings)
        if row_count == 0:
            raise ValueError("file has a header but no data rows")
        _finish_attempt(settings, run_id, metadata, row_count)
    except Exception as exc:
        log.error("Ingestion run %s failed: %s", run_id, exc)
        _record_failure(settings, run_id, exc)
        raise

    log.info("Ingestion run %s succeeded: %d rows", run_id, row_count)
    return IngestionResult(run_id, "succeeded", sha256, row_count)
