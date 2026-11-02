# Architecture decisions

## D1: DuckDB and Parquet first, PostgreSQL deferred
DuckDB queries CSV and Parquet files directly, works on data larger than RAM
and needs no server. This suits a local-first project on modest hardware.
PostgreSQL will be added only if a serving or metadata database is justified.

## D2: Custom validation checks before Great Expectations
Custom Python and SQL checks are lighter and teach the underlying concepts.
dbt tests will cover model-level checks later.

## D3: One dataset at a time
SGCC is processed first. REWDP and TDD2022 are added only after the SGCC
pipeline is reliable. Customer IDs are never merged across datasets.

## D4: No heavy infrastructure or ML
Spark, Kafka, Kubernetes and cloud services are out of scope. Any ML is a
small, optional baseline, because the project focus is data engineering.

## D5: Raw data is immutable
Original downloaded files are never modified. All cleaning writes to new
locations.
