"""Profile the readings (cells) of the raw SGCC CSV. Read-only.

The wide table is unpivoted to one row per (customer, date) and aggregated
in a streaming way, so the 43.8 million cells are never held in memory.
"""

from __future__ import annotations

import duckdb

from gridpulse.config import load_settings
from gridpulse.utils.logging_setup import get_logger, setup_logging

CSV_NAME = "data set.csv"
ID_COL = "CONS_NO"
LABEL_COL = "FLAG"

log = get_logger("gridpulse.profile_sgcc_cells")

# INCLUDE NULLS is essential: by default UNPIVOT silently drops NULL cells,
# which would hide exactly the missing readings we want to count.
UNPIVOT_FROM = f"""
    FROM raw
    UNPIVOT INCLUDE NULLS (
        reading_raw FOR reading_date
        IN (COLUMNS(* EXCLUDE ("{ID_COL}", "{LABEL_COL}")))
    )
"""

SUMMARY_SQL = f"""
WITH cells AS (
    SELECT "{LABEL_COL}" AS flag,
           reading_raw,
           try_cast(reading_raw AS DOUBLE) AS num
    {UNPIVOT_FROM}
)
SELECT
    flag,
    count(*)                                                        AS total_cells,
    count_if(reading_raw IS NULL)                                   AS null_cells,
    round(100.0 * count_if(reading_raw IS NULL) / count(*), 2)      AS null_pct,
    count_if(reading_raw IS NOT NULL AND trim(reading_raw) = '')    AS blank_text,
    count_if(reading_raw IS NOT NULL AND trim(reading_raw) <> ''
             AND num IS NULL)                                       AS non_numeric,
    count_if(isnan(num))                                            AS nan_cells,
    count_if(isinf(num))                                            AS inf_cells,
    count_if(num < 0)                                               AS negative,
    count_if(num = 0)                                               AS zeros,
    count_if(num > 0)                                               AS positive,
    min(num) FILTER (WHERE isfinite(num))                           AS min_value,
    max(num) FILTER (WHERE isfinite(num))                           AS max_value,
    approx_quantile(num, 0.5)   FILTER (WHERE isfinite(num))        AS median_approx,
    approx_quantile(num, 0.99)  FILTER (WHERE isfinite(num))        AS p99_approx,
    approx_quantile(num, 0.999) FILTER (WHERE isfinite(num))        AS p999_approx
FROM cells
GROUP BY ROLLUP (flag)
ORDER BY flag NULLS LAST
"""

EXAMPLES_SQL = f"""
WITH cells AS (
    SELECT reading_raw, try_cast(reading_raw AS DOUBLE) AS num
    {UNPIVOT_FROM}
)
SELECT reading_raw, count(*) AS n
FROM cells
WHERE reading_raw IS NOT NULL AND (num IS NULL OR NOT isfinite(num))
GROUP BY reading_raw
ORDER BY n DESC
LIMIT 10
"""


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level, settings.log_dir / "profile_sgcc_cells.log")

    csv_path = settings.raw_dir / "sgcc" / CSV_NAME
    if not csv_path.exists():
        raise FileNotFoundError(f"Expected raw file not found: {csv_path}")

    tmp_dir = settings.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()  # in-memory database
    con.execute(f"SET memory_limit = '{settings.duckdb_memory_limit}'")
    con.execute(f"SET threads = {settings.duckdb_threads}")
    con.execute(f"SET temp_directory = '{tmp_dir}'")

    path_sql = str(csv_path).replace("'", "''")
    con.execute(
        f"CREATE VIEW raw AS "
        f"SELECT * FROM read_csv('{path_sql}', header = true, all_varchar = true)"
    )

    n_rows = con.execute("SELECT count(*) FROM raw").fetchone()[0]
    n_date_cols = len(con.execute("DESCRIBE raw").fetchall()) - 2
    expected_cells = n_rows * n_date_cols
    log.info("Rows: %d, date columns: %d, expected cells: %d",
             n_rows, n_date_cols, expected_cells)

    log.info("Aggregating readings (this scans the whole file)...")
    cursor = con.execute(SUMMARY_SQL)
    names = [d[0] for d in cursor.description]
    rows = cursor.fetchall()

    for row in rows:
        label = "ALL" if row[0] is None else f"FLAG={row[0]}"
        log.info("--- %s ---", label)
        for name, value in zip(names[1:], row[1:]):
            log.info("%-14s %s", name, value)

    all_row = next(r for r in rows if r[0] is None)
    if all_row[1] != expected_cells:
        raise RuntimeError(
            f"Cell count mismatch: counted {all_row[1]}, expected {expected_cells}. "
            "Some cells were dropped by the unpivot."
        )
    log.info("Sanity check passed: counted cells equal rows x date columns")

    log.info("Most common non-numeric / non-finite text values:")
    examples = con.execute(EXAMPLES_SQL).fetchall()
    if not examples:
        log.info("  none")
    for value, n in examples:
        log.info("  %r: %d", value, n)

    con.close()
    log.info("Cell profile complete")


if __name__ == "__main__":
    main()
