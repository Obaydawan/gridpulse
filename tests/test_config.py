from gridpulse.config import load_settings


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
