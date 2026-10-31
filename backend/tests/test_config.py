from app.config import Settings, load_settings


def test_defaults_bind_localhost(monkeypatch):
    for key in ("AOC_HOST", "AOC_PORT", "AOC_DATA_DIR", "AOC_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.effective_database_url.endswith("/data/answeroncue.db")


def test_env_prefix(monkeypatch):
    monkeypatch.setenv("AOC_PORT", "9001")
    assert Settings(_env_file=None).port == 9001


def test_keys_are_secret(monkeypatch):
    monkeypatch.setenv("AOC_OPENAI_API_KEY", "sk-test")
    settings = Settings(_env_file=None)
    assert "sk-test" not in repr(settings)


def test_empty_env_file_values_mean_default(tmp_path, monkeypatch):
    # env-example.txt has empty lines like "AOC_DATA_DIR="; copied as-is they must not
    # turn the data folder into the current directory.
    for key in ("AOC_DATA_DIR", "AOC_DATABASE_URL", "AOC_PORT"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("AOC_DATA_DIR=\nAOC_DATABASE_URL=\nAOC_PORT=\n", encoding="utf-8")
    settings = load_settings(base_env_file=env)
    assert settings.data_dir == Settings.model_fields["data_dir"].default
    assert settings.port == 8000
    assert settings.effective_database_url.endswith("/data/answeroncue.db")
