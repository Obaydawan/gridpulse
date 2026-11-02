# GridPulse architecture (initial)

Data flow:

    Data sources (SGCC first)
            |
            v
    Python ingestion  -->  data/raw/<source>/   (immutable originals)
            |
            v
    Validation and quality checks
            |
            v
    data/staging/   (cleaned Parquet)
            |
            v
    DuckDB analytical tables   (dbt models added in Phase 4)
            |
            v
    Quality reports and consumption summaries

Later phases add Docker Compose, Airflow orchestration and a Streamlit
monitoring dashboard.
