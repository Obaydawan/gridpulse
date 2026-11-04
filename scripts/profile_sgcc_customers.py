"""Profile missingness and unusual readings per customer. Read-only.

One streaming pass over the unpivoted wide table builds a small per-customer
summary table (about 42,000 rows). Every report below is computed from that
summary, so the heavy scan happens only once.

Gap vocabulary (positions are column positions, in file order):
  leading   missing cells before the customer's first reading
  trailing  missing cells after the customer's last reading
  interior  missing cells between the first and last reading
"""

from __future__ import annotations

from datetime import datetime

import duckdb

from gridpulse.config import load_settings
from gridpulse.utils.logging_setup import get_logger, setup_logging

CSV_NAME = "data set.csv"
ID_COL = "CONS_NO"
LABEL_COL = "FLAG"
DATE_FORMAT = "%m/%d/%Y"

log = get_logger("gridpulse.profile_sgcc_customers")


def log_rows(title: str, cursor: duckdb.DuckDBPyConnection) -> None:
    """Log a query result as a simple aligned table."""
    names = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    log.info("=== %s ===", title)
    log.info(" | ".join(f"{n:>20}" for n in names))
    for row in rows:
        log.info(" | ".join(f"{str(v):>20}" for v in row))


def build_calendar(con: duckdb.DuckDBPyConnection) -> int:
    """Map each date column name to its position (1-based) and parsed date."""
    cols = [
        r[0]
        for r in con.execute("DESCRIBE raw").fetchall()
        if r[0] not in (ID_COL, LABEL_COL)
    ]
    rows = [
        (name, idx, datetime.strptime(name, DATE_FORMAT).date())
        for idx, name in enumerate(cols, start=1)
    ]
    dates = [r[2] for r in rows]
    if dates != sorted(dates):
        raise ValueError("Date columns are not in ascending order")

    con.execute("CREATE TEMP TABLE calendar (col_name VARCHAR, idx INTEGER, d DATE)")
    con.executemany("INSERT INTO calendar VALUES (?, ?, ?)", rows)
    return len(rows)


def build_customer_summary(con: duckdb.DuckDBPyConnection, n_cols: int) -> int:
    """One scan of the whole file -> one summary row per customer."""
    # INCLUDE NULLS keeps missing cells in the unpivot, so they are counted.
    con.execute(
        f"""
        CREATE TEMP TABLE cust AS
        SELECT u.cons_no,
               u.flag,
               count(*)                                              AS cols_seen,
               count(u.reading_raw)                                  AS present,
               {n_cols} - count(u.reading_raw)                       AS missing,
               min(c.idx) FILTER (WHERE u.reading_raw IS NOT NULL)   AS first_idx,
               max(c.idx) FILTER (WHERE u.reading_raw IS NOT NULL)   AS last_idx,
               count_if(u.num = 0)                                   AS zeros,
               max(u.num)                                            AS max_val,
               avg(u.num)                                            AS mean_val,
               stddev_samp(u.num)                                    AS sd_val
        FROM (
            SELECT "{ID_COL}" AS cons_no,
                   "{LABEL_COL}" AS flag,
                   reading_date,
                   reading_raw,
                   try_cast(reading_raw AS DOUBLE) AS num
            FROM raw
            UNPIVOT INCLUDE NULLS (
                reading_raw FOR reading_date
                IN (COLUMNS(* EXCLUDE ("{ID_COL}", "{LABEL_COL}")))
            )
        ) u
        JOIN calendar c ON c.col_name = u.reading_date
        GROUP BY u.cons_no, u.flag
        """
    )
    n_customers, min_seen, max_seen = con.execute(
        "SELECT count(*), min(cols_seen), max(cols_seen) FROM cust"
    ).fetchone()
    if min_seen != n_cols or max_seen != n_cols:
        raise RuntimeError(
            f"Each customer should have {n_cols} cells but saw {min_seen}..{max_seen}"
        )
    return n_customers


def report_missing_buckets(con, n: int) -> None:
    cursor = con.execute(
        f"""
        SELECT bucket,
               count_if(flag = '0') AS flag_0,
               count_if(flag = '1') AS flag_1,
               count(*)             AS all_customers
        FROM (
            SELECT flag,
                   CASE WHEN present = 0 THEN '7: 100% (no readings)'
                        WHEN missing = 0 THEN '1: 0% (complete)'
                        WHEN missing * 100.0 / {n} <= 10 THEN '2: >0-10%'
                        WHEN missing * 100.0 / {n} <= 25 THEN '3: >10-25%'
                        WHEN missing * 100.0 / {n} <= 50 THEN '4: >25-50%'
                        WHEN missing * 100.0 / {n} <= 75 THEN '5: >50-75%'
                        ELSE '6: >75% but not 100%' END AS bucket
            FROM cust
        )
        GROUP BY bucket
        ORDER BY bucket
        """
    )
    log_rows("Customers by share of missing readings", cursor)


def report_special_groups(con) -> None:
    cursor = con.execute(
        """
        SELECT 'no readings at all' AS grp,
               count_if(flag = '0') AS flag_0, count_if(flag = '1') AS flag_1,
               count(*) AS all_customers
        FROM cust WHERE present = 0
        UNION ALL
        SELECT 'exactly one reading',
               count_if(flag = '0'), count_if(flag = '1'), count(*)
        FROM cust WHERE present = 1
        UNION ALL
        SELECT 'all readings are zero',
               count_if(flag = '0'), count_if(flag = '1'), count(*)
        FROM cust WHERE present > 0 AND max_val = 0
        UNION ALL
        SELECT 'constant non-zero readings',
               count_if(flag = '0'), count_if(flag = '1'), count(*)
        FROM cust WHERE present > 1 AND sd_val = 0 AND max_val > 0
        """
    )
    log_rows("Special customer groups", cursor)


def report_gap_structure(con, n: int) -> None:
    cursor = con.execute(
        f"""
        SELECT flag,
               sum(missing)                                        AS missing_cells,
               sum(missing) FILTER (WHERE present = 0)             AS in_empty_customers,
               sum(first_idx - 1) FILTER (WHERE present > 0)       AS leading,
               sum({n} - last_idx) FILTER (WHERE present > 0)      AS trailing,
               sum(missing - (first_idx - 1) - ({n} - last_idx))
                   FILTER (WHERE present > 0)                      AS interior
        FROM cust
        GROUP BY ROLLUP (flag)
        ORDER BY flag NULLS LAST
        """
    )
    log_rows("Where the missing cells are (NULL flag = all customers)", cursor)


def report_extremes(con) -> None:
    cursor = con.execute(
        """
        SELECT flag,
               count(*)                       AS customers,
               count_if(max_val > 1000)       AS max_gt_1k,
               count_if(max_val > 10000)      AS max_gt_10k,
               count_if(max_val > 100000)     AS max_gt_100k
        FROM cust
        GROUP BY ROLLUP (flag)
        ORDER BY flag NULLS LAST
        """
    )
    log_rows("Customers whose maximum reading exceeds a threshold", cursor)

    cursor = con.execute(
        """
        SELECT cons_no, flag, present, round(max_val, 2) AS max_val
        FROM cust
        ORDER BY max_val DESC NULLS LAST
        LIMIT 5
        """
    )
    log_rows("Five customers with the highest single reading", cursor)


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level, settings.log_dir / "profile_sgcc_customers.log")

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

    n_cols = build_calendar(con)
    log.info("Date columns: %d. Building per-customer summary (one full scan)...", n_cols)
    n_customers = build_customer_summary(con, n_cols)
    log.info("Customer summary ready: %d customers", n_customers)

    report_missing_buckets(con, n_cols)
    report_special_groups(con)
    report_gap_structure(con, n_cols)
    report_extremes(con)

    con.close()
    log.info("Customer profile complete")


if __name__ == "__main__":
    main()
