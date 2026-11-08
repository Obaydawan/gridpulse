# GridPulse

An electricity data engineering Projec: ingestion, validation, transformation
and analytics for electricity consumption datasets.

> Status: in development (Phase 0: project setup).

## Goal

Build a reproducible, locally runnable data pipeline that ingests electricity
consumption data, validates it, models it for analytics, and produces
data-quality and consumption reports. It is also the data foundation for
future electricity-theft research.

## Planned architecture

Source files -> Python ingestion -> raw storage -> validation
-> staging (Parquet) -> analytical models (DuckDB, dbt) -> reports

Orchestration (Airflow), containers (Docker) and a monitoring dashboard
(Streamlit) are added in later phases.

## Datasets and limitations

- **SGCC Electricity Theft Detection dataset**: Chinese (State Grid Corporation
  of China) consumer data, used as a public research benchmark. It is NOT
  Pakistani data.
- **REWDP (LUMS)**: Pakistani household data, planned for a later phase. It has
  no confirmed theft labels.
- Datasets are not stored in this repository. Check each source for its license.
- This project does not claim to detect real electricity theft in Pakistan.

## Setup

Setup instructions will be added as the project grows.

## Roadmap

- [ ] Phase 0: Repository and environment setup
- [ ] Phase 1: Dataset inspection and ingestion
- [ ] Phase 2: Data quality engineering
- [ ] Phase 3: Transformation and storage
- [ ] Phase 4: dbt models
- [ ] Phase 5: Docker and Airflow orchestration
- [ ] Phase 6: Monitoring and dashboard
- [ ] Phase 7: Pakistani household data (REWDP)
- [ ] Phase 8: Testing, documentation and demo
