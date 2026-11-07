import uuid
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from gridpulse.config import load_settings
from gridpulse.ingestion.registry import (
    connect_registry,
    initialize_registry,
    utc_now,
)

EXPECTED_COLUMNS = {
    "run_id",
    "source_name",
    "file_name",
    "file_path",
    "file_size_bytes",
    "sha256",
    "row_count",
    "n_date_columns",
    "first_date",
    "last_date",
    "status",
    "started_at",
    "completed_at",
    "error_message",
}

T0 = datetime(2026, 11, 6, 12, 0, 0)
T1 = datetime(2026, 11, 6, 12, 0, 5)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    """Settings whose data directory is a throwaway folder, never the real one."""
    monkeypatch.setenv("GRIDPULSE_DATA_DIR", str(tmp_path))
    return load_settings()


@pytest.fixture
def con(settings):
    initialize_registry(settings)
    connection = connect_registry(settings)
    yield connection
    connection.close()


def started_row(**overrides):
    """A valid 'started' row. Pass overrides to change or break fields."""
    row = {
        "run_id": uuid.uuid4(),
        "source_name": "sgcc",
        "file_name": "data set.csv",
        "file_path": "data/raw/sgcc/data set.csv",
        "file_size_bytes": 1234,
        "sha256": "a" * 64,
        "status": "started",
        "started_at": T0,
    }
    row.update(overrides)
    return row


def succeeded_row(**overrides):
    return started_row(
        **{"status": "succeeded", "row_count": 42372, "completed_at": T1, **overrides}
    )


def failed_row(**overrides):
    return started_row(
        **{"status": "failed", "error_message": "boom", "completed_at": T1, **overrides}
    )


def insert(con, row):
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    con.execute(
        f"INSERT INTO ingestion_runs ({columns}) VALUES ({placeholders})",
        list(row.values()),
    )


def test_initialize_creates_database_at_configured_path(settings):
    path = initialize_registry(settings)

    assert path == settings.data_dir / "warehouse" / "pipeline.duckdb"
    assert path.exists()


def test_table_has_exactly_the_agreed_columns(con):
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ingestion_runs'"
    ).fetchall()

    assert {name for (name,) in rows} == EXPECTED_COLUMNS


def test_initialize_twice_is_safe_and_keeps_existing_rows(settings):
    initialize_registry(settings)
    first = connect_registry(settings)
    insert(first, started_row())
    first.close()

    initialize_registry(settings)

    second = connect_registry(settings)
    try:
        (count,) = second.execute("SELECT count(*) FROM ingestion_runs").fetchone()
    finally:
        second.close()
    assert count == 1


def test_valid_rows_in_every_status_are_accepted(con):
    insert(con, started_row())
    insert(con, succeeded_row())
    insert(con, failed_row())

    (count,) = con.execute("SELECT count(*) FROM ingestion_runs").fetchone()
    assert count == 3


INVALID_ROWS = {
    "unknown status": started_row(status="weird"),
    "started row with a completion time": started_row(completed_at=T1),
    "succeeded without a completion time": succeeded_row(completed_at=None),
    "failed without a completion time": failed_row(completed_at=None),
    "succeeded without a row count": succeeded_row(row_count=None),
    "failed without an error message": failed_row(error_message=None),
    "missing source name": started_row(source_name=None),
    "missing sha256": started_row(sha256=None),
}


@pytest.mark.parametrize("row", INVALID_ROWS.values(), ids=INVALID_ROWS.keys())
def test_invalid_rows_are_rejected(con, row):
    with pytest.raises(duckdb.ConstraintException):
        insert(con, row)

    (count,) = con.execute("SELECT count(*) FROM ingestion_runs").fetchone()
    assert count == 0


def test_duplicate_run_id_is_rejected(con):
    run_id = uuid.uuid4()
    insert(con, started_row(run_id=run_id))

    with pytest.raises(duckdb.ConstraintException):
        insert(con, started_row(run_id=run_id))


def test_a_started_row_can_be_completed_once_by_update(con):
    run_id = uuid.uuid4()
    insert(con, started_row(run_id=run_id))

    con.execute(
        "UPDATE ingestion_runs "
        "SET status = 'succeeded', row_count = ?, completed_at = ? "
        "WHERE run_id = ?",
        [42372, T1, run_id],
    )

    status, row_count = con.execute(
        "SELECT status, row_count FROM ingestion_runs WHERE run_id = ?", [run_id]
    ).fetchone()
    assert (status, row_count) == ("succeeded", 42372)


def test_update_cannot_create_an_invalid_row(con):
    run_id = uuid.uuid4()
    insert(con, started_row(run_id=run_id))

    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            "UPDATE ingestion_runs SET status = 'succeeded', completed_at = ? "
            "WHERE run_id = ?",
            [T1, run_id],  # succeeded without a row_count
        )


def test_timestamps_round_trip_unchanged(con):
    stamp = datetime(2026, 11, 6, 12, 34, 56, 789012)
    insert(con, started_row(started_at=stamp))

    (stored,) = con.execute("SELECT started_at FROM ingestion_runs").fetchone()
    assert stored == stamp
    assert stored.tzinfo is None


def test_utc_now_is_naive_and_matches_real_utc():
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    stamp = utc_now()
    after = datetime.now(timezone.utc).replace(tzinfo=None)

    assert stamp.tzinfo is None
    assert before - timedelta(seconds=1) <= stamp <= after + timedelta(seconds=1)


def test_connection_applies_configured_thread_limit(settings):
    connection = connect_registry(settings)
    try:
        (threads,) = connection.execute(
            "SELECT current_setting('threads')"
        ).fetchone()
    finally:
        connection.close()

    assert int(threads) == settings.duckdb_threads
