"""Profile the structure of the raw SGCC CSV: header dates, ids and labels.

Read-only: this script never modifies the raw data. Cell-level analysis
(missing values, negatives, outliers) comes in a later step.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

import duckdb

from gridpulse.config import load_settings
from gridpulse.utils.logging_setup import get_logger, setup_logging

CSV_NAME = "data set.csv"
ID_COL = "CONS_NO"
LABEL_COL = "FLAG"
DATE_FORMAT = "%m/%d/%Y"

log = get_logger("gridpulse.profile_sgcc")


def analyse_header(csv_path) -> list[str]:
    """Read the raw first line and report on its structure."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        first_line = f.readline()

    log.info("Header starts with BOM: %s", first_line.startswith("\ufeff"))
    log.info("Header uses CRLF line ending: %s", first_line.endswith("\r\n"))

    header = first_line.lstrip("\ufeff").rstrip("\r\n").split(",")
    log.info("Header fields: %d", len(header))
    log.info("Position of %s: %s", ID_COL, header.index(ID_COL) if ID_COL in header else "MISSING")
    log.info("Position of %s: %s", LABEL_COL, header.index(LABEL_COL) if LABEL_COL in header else "MISSING")

    name_counts = Counter(header)
    duplicates = [name for name, n in name_counts.items() if n > 1]
    log.info("Duplicate header names: %s", duplicates or "none")

    date_cols = [h for h in header if h not in (ID_COL, LABEL_COL)]
    parsed: list[date] = []
    unparseable: list[str] = []
    for name in date_cols:
        try:
            parsed.append(datetime.strptime(name, DATE_FORMAT).date())
        except ValueError:
            unparseable.append(name)

    log.info("Date-like columns: %d (unparseable: %d)", len(date_cols), len(unparseable))
    if unparseable:
        log.warning("Unparseable column names (first 10): %s", unparseable[:10])

    if parsed:
        first_date, last_date = min(parsed), max(parsed)
        expected_days = (last_date - first_date).days + 1
        calendar = {first_date + timedelta(days=i) for i in range(expected_days)}
        missing = sorted(calendar - set(parsed))
        log.info("Date range: %s to %s", first_date, last_date)
        log.info("Calendar days in range: %d, date columns parsed: %d", expected_days, len(parsed))
        log.info("Duplicate dates: %d", len(parsed) - len(set(parsed)))
        log.info("Dates in ascending header order: %s", parsed == sorted(parsed))
        log.info("Calendar dates with no column: %s", [d.isoformat() for d in missing] or "none")

    return header


def analyse_ids_and_labels(con: duckdb.DuckDBPyConnection, csv_path) -> None:
    """Scan the CSV once, keeping only the id and label columns."""
    path_sql = str(csv_path).replace("'", "''")
    con.execute(
        f"""
        CREATE TEMP TABLE ids AS
        SELECT "{ID_COL}" AS cons_no, "{LABEL_COL}" AS flag
        FROM read_csv('{path_sql}', header = true, all_varchar = true)
        """
    )

    total, non_null, distinct, blank = con.execute(
        """
        SELECT count(*),
               count(cons_no),
               count(DISTINCT cons_no),
               sum(CASE WHEN cons_no IS NULL OR trim(cons_no) = '' THEN 1 ELSE 0 END)
        FROM ids
        """
    ).fetchone()
    log.info("Data rows: %d", total)
    log.info("Customer ids: non-null %d, distinct %d, blank/null %d", non_null, distinct, blank)
    log.info("Duplicate customer ids: %d", non_null - distinct)

    rows = con.execute(
        "SELECT flag, count(*) AS n FROM ids GROUP BY flag ORDER BY flag"
    ).fetchall()
    for flag, n in rows:
        log.info("FLAG = %r: %d rows (%.2f%%)", flag, n, 100.0 * n / total)


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level, settings.log_dir / "profile_sgcc.log")

    csv_path = settings.raw_dir / "sgcc" / CSV_NAME
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected raw file not found: {csv_path}")
    log.info("Profiling %s (%.1f MB)", csv_path, csv_path.stat().st_size / 1_048_576)

    analyse_header(csv_path)

    con = duckdb.connect()  # in-memory database
    con.execute(f"SET memory_limit = '{settings.duckdb_memory_limit}'")
    con.execute(f"SET threads = {settings.duckdb_threads}")
    analyse_ids_and_labels(con, csv_path)
    con.close()
    log.info("Profile complete")


if __name__ == "__main__":
    main()
