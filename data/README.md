# Data directory

Datasets are NOT stored in Git. This folder layout is created locally:

- `raw/`        original downloaded files, never modified
- `staging/`    cleaned data (Parquet)
- `processed/`  analytical outputs
- `warehouse/`  DuckDB database files

See the main README for dataset sources and licenses.
