"""Logs estructurados (una línea JSON por evento) con un trace_id que une todo lo de una ejecución."""

import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import IO, Any

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")
_MARK = "_tradebot_handler"


def current_trace_id() -> str:
    return _trace_id.get()


@contextmanager
def trace(trace_id: str | None = None) -> Iterator[str]:
    """Todo lo que se registre dentro del bloque lleva este id (por defecto, uno nuevo y corto)."""
    tid = trace_id or uuid.uuid4().hex[:12]
    token = _trace_id.set(tid)
    try:
        yield tid
    finally:
        _trace_id.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": current_trace_id(),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> None:
    """Idempotente: reemplaza el manejador anterior de tradebot en vez de duplicarlo."""
    lvl = logging.getLevelNamesMapping().get(level.upper())
    if lvl is None:
        raise ValueError(f"nivel de log inválido: {level!r}")
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _MARK, True)
    root.addHandler(handler)
    root.setLevel(lvl)
