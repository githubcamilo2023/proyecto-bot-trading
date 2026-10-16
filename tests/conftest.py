import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Cada test corre en un directorio temporal y sin variables de tradebot heredadas:
    ninguna ruta por defecto (data/…) puede ensuciar el proyecto ni filtrarse entre tests."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRADEBOT_KEY", raising=False)
    monkeypatch.delenv("TRADEBOT_LOG_LEVEL", raising=False)
