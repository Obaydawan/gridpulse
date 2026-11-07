import dataclasses
import hashlib
import logging
import uuid
from datetime import date

import duckdb
import pytest

import gridpulse.ingestion.registry as registry
from gridpulse.config import load_settings
from gridpulse.ingestion.registry import (
    FileMetadata,
    hash_file,
    initialize_registry,
    register_file,
)

SGCC_LIKE_CSV = "1/1/2014,1/2/2014,CONS_NO,FLAG\n1.5,2.0,AAA,0\n,3.0,BBB,1\n0,0,CCC,0\n"


@pytest.fixture
def settings(monkeypatch, tmp_path):
    """Settings where both the data dir and the project root are throwaway folders."""
    monkeypatch.setenv("GRIDPULSE_DATA_DIR", str(tmp_path / "data"))
    return dataclasses.replace(load_settings(), project_root=tmp_path)


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "data" / "raw" / "sgcc" / "data set.csv"
    path.parent.mkdir(parents=True)
    path.write_text(SGCC_LIKE_CSV, encoding="utf-8")
    return path


def ok_validator(path):
    return FileMetadata(
        n_date_columns=2, first_date=date(2014, 1, 1), last_date=date(2014, 1, 2)
    )


def fetch_runs(settings):
    """All registry rows, oldest first, as dicts."""
    con = registry.connect_registry(settings)
    try:
        cursor = con.execute("SELECT * FROM ingestion_runs ORDER BY started_at")
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
    finally:
        con.close()


# ---------- hashing ----------


def test_hash_file_matches_known_sha256(tmp_path):
    path = tmp_path / "abc.txt"
    path.write_bytes(b"abc")

    assert hash_file(path) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_hash_does_not_depend_on_chunk_size(tmp_path):
    data = bytes(range(256)) * 50
    path = tmp_path / "blob.bin"
    path.write_bytes(data)

    assert hash_file(path, chunk_size=1) == hashlib.sha256(data).hexdigest()
    assert hash_file(path, chunk_size=7) == hashlib.sha256(data).hexdigest()
    assert hash_file(path) == hashlib.sha256(data).hexdigest()


# ---------- the happy path ----------


def test_first_registration_succeeds_and_stores_metadata(settings, csv_file):
    result = register_file(settings, "sgcc", csv_file, ok_validator)

    assert result.outcome == "succeeded"
    assert result.row_count == 3
    assert result.sha256 == hash_file(csv_file)

    (row,) = fetch_runs(settings)
    assert row["run_id"] == result.run_id
    assert row["status"] == "succeeded"
    assert row["source_name"] == "sgcc"
    assert row["file_name"] == "data set.csv"
    assert row["file_path"] == "data/raw/sgcc/data set.csv"
    assert row["file_size_bytes"] == csv_file.stat().st_size
    assert row["row_count"] == 3
    assert row["n_date_columns"] == 2
    assert row["first_date"] == date(2014, 1, 1)
    assert row["last_date"] == date(2014, 1, 2)
    assert row["error_message"] is None
    assert row["started_at"] <= row["completed_at"]


def test_relative_path_is_resolved_against_project_root(settings, csv_file):
    result = register_file(
        settings, "sgcc", "data/raw/sgcc/data set.csv", ok_validator
    )

    assert result.outcome == "succeeded"


def test_file_outside_project_root_stores_absolute_path(settings, tmp_path):
    outside = tmp_path / "elsewhere" / "other.csv"
    outside.parent.mkdir()
    outside.write_text(SGCC_LIKE_CSV, encoding="utf-8")
    inside_root = dataclasses.replace(settings, project_root=tmp_path / "project")

    register_file(inside_root, "sgcc", outside, ok_validator)

    (row,) = fetch_runs(inside_root)
    assert row["file_path"] == str(outside.resolve())


# ---------- idempotency: running twice must not duplicate anything ----------


def test_running_twice_skips_and_creates_no_new_row(settings, csv_file):
    first = register_file(settings, "sgcc", csv_file, ok_validator)
    second = register_file(settings, "sgcc", csv_file, ok_validator)

    assert first.outcome == "succeeded"
    assert second.outcome == "skipped"
    assert second.run_id == first.run_id  # points at the run that supplied the data
    assert second.row_count == first.row_count
    assert second.sha256 == first.sha256
    assert len(fetch_runs(settings)) == 1


def test_skip_does_not_run_the_validator(settings, csv_file):
    register_file(settings, "sgcc", csv_file, ok_validator)

    def must_not_be_called(path):
        raise AssertionError("validator should not run for a skipped file")

    result = register_file(settings, "sgcc", csv_file, must_not_be_called)
    assert result.outcome == "skipped"


def test_identical_content_under_another_filename_is_skipped(
    settings, csv_file, tmp_path
):
    register_file(settings, "sgcc", csv_file, ok_validator)
    copy = tmp_path / "data" / "raw" / "sgcc" / "renamed.csv"
    copy.write_text(SGCC_LIKE_CSV, encoding="utf-8")

    result = register_file(settings, "sgcc", copy, ok_validator)

    assert result.outcome == "skipped"
    assert len(fetch_runs(settings)) == 1


def test_identical_content_for_another_source_is_registered_again(
    settings, csv_file
):
    register_file(settings, "sgcc", csv_file, ok_validator)

    result = register_file(settings, "other_source", csv_file, ok_validator)

    assert result.outcome == "succeeded"
    assert len(fetch_runs(settings)) == 2


def test_changed_content_at_same_path_is_a_new_run_with_warning(
    settings, csv_file, caplog
):
    register_file(settings, "sgcc", csv_file, ok_validator)
    csv_file.write_text(SGCC_LIKE_CSV + "9,9,DDD,1\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = register_file(settings, "sgcc", csv_file, ok_validator)

    assert result.outcome == "succeeded"
    assert result.row_count == 4
    assert len(fetch_runs(settings)) == 2
    assert any("DIFFERENT content" in record.message for record in caplog.records)


# ---------- inputs that must not create rows ----------


def test_missing_file_raises_and_creates_no_row(settings, tmp_path):
    with pytest.raises(FileNotFoundError):
        register_file(settings, "sgcc", tmp_path / "nope.csv", ok_validator)

    initialize_registry(settings)
    assert fetch_runs(settings) == []


def test_empty_source_name_is_rejected(settings, csv_file):
    with pytest.raises(ValueError, match="source_name"):
        register_file(settings, "  ", csv_file, ok_validator)

    initialize_registry(settings)
    assert fetch_runs(settings) == []


# ---------- failures ----------


class BadFileError(ValueError):
    pass


def test_validator_failure_is_recorded_and_the_original_error_is_raised(
    settings, csv_file
):
    def failing_validator(path):
        raise BadFileError("header is wrong")

    with pytest.raises(BadFileError, match="header is wrong"):
        register_file(settings, "sgcc", csv_file, failing_validator)

    (row,) = fetch_runs(settings)
    assert row["status"] == "failed"
    assert "BadFileError" in row["error_message"]
    assert "header is wrong" in row["error_message"]
    assert row["completed_at"] is not None
    assert row["row_count"] is None


def test_a_failed_attempt_does_not_block_a_retry(settings, csv_file):
    def failing_validator(path):
        raise BadFileError("first attempt fails")

    with pytest.raises(BadFileError):
        register_file(settings, "sgcc", csv_file, failing_validator)

    retry = register_file(settings, "sgcc", csv_file, ok_validator)
    third = register_file(settings, "sgcc", csv_file, ok_validator)

    assert retry.outcome == "succeeded"
    assert third.outcome == "skipped"
    assert [r["status"] for r in fetch_runs(settings)] == ["failed", "succeeded"]


def test_header_only_file_is_a_failure_not_a_success(settings, tmp_path):
    empty = tmp_path / "data" / "raw" / "empty.csv"
    empty.parent.mkdir(parents=True)
    empty.write_text("1/1/2014,CONS_NO,FLAG\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no data rows"):
        register_file(settings, "sgcc", empty, ok_validator)

    (row,) = fetch_runs(settings)
    assert row["status"] == "failed"


def test_unreadable_csv_is_recorded_as_failed(settings, tmp_path):
    broken = tmp_path / "data" / "raw" / "broken.csv"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"")  # a completely empty file

    with pytest.raises(Exception):
        register_file(settings, "sgcc", broken, ok_validator)

    (row,) = fetch_runs(settings)
    assert row["status"] == "failed"
    assert row["error_message"]


def test_stale_started_row_does_not_block_a_new_attempt(settings, csv_file):
    initialize_registry(settings)
    sha256 = hash_file(csv_file)
    con = registry.connect_registry(settings)
    con.execute(
        "INSERT INTO ingestion_runs (run_id, source_name, file_name, file_path, "
        "file_size_bytes, sha256, status, started_at) "
        "VALUES (?, 'sgcc', 'data set.csv', 'x', 1, ?, 'started', ?)",
        [uuid.uuid4(), sha256, registry.utc_now()],
    )
    con.close()

    result = register_file(settings, "sgcc", csv_file, ok_validator)

    assert result.outcome == "succeeded"
    assert sorted(r["status"] for r in fetch_runs(settings)) == [
        "started",
        "succeeded",
    ]


def test_interrupted_run_stays_started(settings, csv_file):
    def interrupted(path):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        register_file(settings, "sgcc", csv_file, interrupted)

    (row,) = fetch_runs(settings)
    assert row["status"] == "started"
    assert row["completed_at"] is None


def test_failure_to_record_failure_does_not_mask_the_original_error(
    settings, csv_file, monkeypatch, caplog
):
    real_connect = registry.connect_registry
    registry_down = {"value": False}

    def flaky_connect(s):
        if registry_down["value"]:
            raise RuntimeError("registry unavailable")
        return real_connect(s)

    monkeypatch.setattr(registry, "connect_registry", flaky_connect)

    def validator_that_also_breaks_the_registry(path):
        registry_down["value"] = True
        raise BadFileError("validator exploded")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(BadFileError, match="validator exploded"):
            register_file(
                settings, "sgcc", csv_file, validator_that_also_breaks_the_registry
            )

    assert any("Could not record the failure" in r.message for r in caplog.records)

    registry_down["value"] = False
    (row,) = fetch_runs(settings)
    assert row["status"] == "started"  # unrecorded, so it stays visible as stale


def test_registry_is_not_left_locked_after_a_run(settings, csv_file):
    register_file(settings, "sgcc", csv_file, ok_validator)

    # A second, independent connection can open and write immediately.
    con = duckdb.connect(str(settings.pipeline_metadata_db))
    try:
        (count,) = con.execute("SELECT count(*) FROM ingestion_runs").fetchone()
    finally:
        con.close()
    assert count == 1
