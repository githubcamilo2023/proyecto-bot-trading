"""Integridad: hashes y firmas HMAC para que la aprobación y la auditoría no se puedan falsificar a mano.

La clave vive en la variable de entorno TRADEBOT_KEY (nunca en el repo ni en una carpeta sincronizada).
Sin clave no se puede aprobar ni verificar una aprobación: el bot falla cerrado.
"""

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from typing import Any

KEY_ENV = "TRADEBOT_KEY"
MIN_KEY_LEN = 32


class MissingKeyError(RuntimeError):
    """No hay clave de integridad válida en el entorno."""


def load_key(env: Mapping[str, str] | None = None) -> bytes:
    raw = (os.environ if env is None else env).get(KEY_ENV, "")
    if len(raw) < MIN_KEY_LEN:
        raise MissingKeyError(
            f"falta {KEY_ENV} (mínimo {MIN_KEY_LEN} caracteres). "
            'Genera una: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return raw.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj: Mapping[str, Any]) -> bytes:
    """JSON determinista: mismas claves/valores -> mismos bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(key: bytes, domain: str, payload: Mapping[str, Any]) -> str:
    """HMAC-SHA256 con separación de dominio: una firma de aprobación no sirve como firma de auditoría."""
    return hmac.new(key, domain.encode("utf-8") + b"\0" + canonical(payload), hashlib.sha256).hexdigest()


def verify(key: bytes, domain: str, payload: Mapping[str, Any], mac: str) -> bool:
    return hmac.compare_digest(sign(key, domain, payload), mac)
