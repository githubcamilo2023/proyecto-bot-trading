import os

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Cada test corre en un directorio temporal y SIN variables de tradebot heredadas de tu consola:
    ninguna ruta por defecto (data/…) ensucia el proyecto y ninguna credencial real (correo, Telegram,
    clave) llega a un test: los tests nunca envían avisos de verdad."""
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("TRADEBOT_") or name == "TRADING_MODE":
            monkeypatch.delenv(name, raising=False)
