"""Registro de auditoría permanente: JSONL de solo-añadir, encadenado y firmado con HMAC.

Cada registro incluye el MAC del anterior (`prev`) y el suyo propio (`mac`). Editar, borrar o reordenar
un registro del medio rompe la cadena y `verify()` lo detecta. Sin la clave no se pueden forjar registros.

Límites conocidos (documentados en docs/threat-model.md):
- Truncar el FINAL del archivo no se detecta por sí solo; guarda `head()` en otro sitio para anclarlo.
- Un solo proceso escritor a la vez; dos escritores simultáneos bifurcan la cadena (verify lo detecta).
"""

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tradebot.integrity import sign, verify
from tradebot.logging_setup import current_trace_id

_DOMAIN = "tradebot/audit/v1"
_MAX_RECORD = 8192  # un registro de auditoría es un hecho, no un volcado de datos
_TAIL_READ = 4 * _MAX_RECORD


class AuditError(Exception):
    """El registro de auditoría no se puede escribir o está corrupto: quien lo usa debe fallar cerrado."""


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    records: int
    error: str | None = None


class AuditLog:
    def __init__(self, path: str | Path, key: bytes) -> None:
        self.path, self._key, self._lock = Path(path), key, threading.Lock()

    def _last(self) -> dict[str, Any] | None:
        """Último registro, validado (firma incluida). Falla cerrado si la cola está corrupta."""
        try:
            with self.path.open("rb") as f:
                size = f.seek(0, os.SEEK_END)
                f.seek(max(0, size - _TAIL_READ))
                chunk = f.read()
        except FileNotFoundError:
            return None
        except OSError as e:  # cualquier otro fallo de disco también es "auditoría no disponible"
            raise AuditError(f"no se pudo leer la auditoría: {e}") from e
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            return None
        try:
            rec = json.loads(lines[-1])
            mac = rec.pop("mac")
            if not verify(self._key, _DOMAIN, rec, mac):
                raise ValueError("firma inválida")
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise AuditError(f"el final del registro de auditoría está corrupto: {e}") from e
        rec["mac"] = mac
        return dict(rec)

    def append(
        self,
        actor: str,
        action: str,
        subject: str,
        result: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Añade un registro y lo fuerza a disco. Lanza AuditError si no se pudo: no se actúa sin rastro."""
        with self._lock:
            last = self._last()
            body: dict[str, Any] = {
                "seq": (last["seq"] + 1) if last else 1,
                "ts": datetime.now(UTC).isoformat(),
                "actor": actor,
                "action": action,
                "subject": subject,
                "result": result,
                "trace_id": current_trace_id(),
                "details": details or {},
                "prev": last["mac"] if last else "",
            }
            record = {**body, "mac": sign(self._key, _DOMAIN, body)}
            line = json.dumps(record, ensure_ascii=False, sort_keys=True)
            if len(line.encode("utf-8")) > _MAX_RECORD:
                raise AuditError("registro de auditoría demasiado grande")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as f:
                    f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                raise AuditError(f"no se pudo escribir la auditoría: {e}") from e
            return record

    def head(self) -> tuple[int, str] | None:
        """(seq, mac) del último registro. Guárdalo fuera de este archivo para detectar truncados."""
        last = self._last()
        return (last["seq"], last["mac"]) if last else None

    def verify(self) -> VerifyResult:
        """Recorre TODA la cadena: formato, firmas, secuencia continua y enlaces entre registros."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return VerifyResult(True, 0)
        except (OSError, UnicodeDecodeError) as e:
            return VerifyResult(False, 0, f"ilegible: {e}")
        prev, n = "", 0
        for i, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                mac = rec.pop("mac")
                good = verify(self._key, _DOMAIN, rec, mac)
            except (ValueError, KeyError, TypeError, AttributeError):
                return VerifyResult(False, n, f"línea {i}: formato inválido")
            if not good:
                return VerifyResult(False, n, f"línea {i}: firma inválida")
            if rec.get("seq") != n + 1:
                return VerifyResult(False, n, f"línea {i}: secuencia rota (registros borrados o reordenados)")
            if rec.get("prev") != prev:
                return VerifyResult(False, n, f"línea {i}: la cadena no enlaza con el registro anterior")
            prev, n = mac, n + 1
        return VerifyResult(True, n)
