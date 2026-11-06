from gridpulse.config import PROJECT_ROOT, load_settings


def test_load_settings_defaults(monkeypatch):
    monkeypatch.delenv("GRIDPULSE_DATA_DIR", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    settings = load_settings()

    assert settings.raw_dir == settings.data_dir / "raw"
    assert settings.duckdb_threads == 2
    assert settings.duckdb_memory_limit == "2GB"
    assert settings.log_level == "INFO"


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIDPULSE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = load_settings()

    assert settings.data_dir == tmp_path
    assert settings.staging_dir == tmp_path / "staging"
    assert settings.log_level == "DEBUG"


def test_pipeline_metadata_db_follows_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIDPULSE_DATA_DIR", str(tmp_path))

    settings = load_settings()

    assert settings.pipeline_metadata_db == (
        tmp_path / "warehouse" / "pipeline.duckdb"
    )


def test_pipeline_metadata_db_uses_configured_relative_path(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("GRIDPULSE_DATA_DIR", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        """
paths:
  data_dir: custom_data
  log_dir: custom_logs
  pipeline_metadata_db: metadata/registry.duckdb

duckdb:
  memory_limit: 1GB
  threads: 1

logging:
  level: WARNING
""",
        encoding="utf-8",
    )

    settings = load_settings(config_file)

    expected_data_dir = PROJECT_ROOT / "custom_data"

    assert settings.data_dir == expected_data_dir
    assert settings.pipeline_metadata_db == (
        expected_data_dir / "metadata" / "registry.duckdb"
    )
    assert settings.log_dir == PROJECT_ROOT / "custom_logs"
    assert settings.duckdb_memory_limit == "1GB"
    assert settings.duckdb_threads == 1
    assert settings.log_level == "WARNING"
