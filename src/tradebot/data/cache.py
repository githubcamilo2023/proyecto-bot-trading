import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Self

from tradebot.models import Candle

_SCHEMA = """CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT, tf TEXT, ts INTEGER, o TEXT, h TEXT, l TEXT, c TEXT, v TEXT,
    PRIMARY KEY (symbol, tf, ts))"""


class CandleCache:
    """Caché local de velas en SQLite (stdlib). Idempotente: guardar dos veces la misma vela no duplica.
    Úsala con `with` para que la conexión se cierre siempre."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def save(self, symbol: str, tf: str, candles: list[Candle]) -> None:
        rows = [
            (symbol, tf, int(c.ts.timestamp() * 1000), *map(str, (c.open, c.high, c.low, c.close, c.volume)))
            for c in candles
        ]
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)", rows)

    def last_ts(self, symbol: str, tf: str) -> datetime | None:
        q = "SELECT MAX(ts) FROM candles WHERE symbol=? AND tf=?"
        (ms,) = self.db.execute(q, (symbol, tf)).fetchone()
        return None if ms is None else datetime.fromtimestamp(ms / 1000, UTC)

    def count(self, symbol: str, tf: str) -> int:
        q = "SELECT COUNT(*) FROM candles WHERE symbol=? AND tf=?"
        return int(self.db.execute(q, (symbol, tf)).fetchone()[0])

    def load(self, symbol: str, tf: str, limit: int) -> list[Candle]:
        """Últimas `limit` velas, en orden ascendente."""
        rows = self.db.execute(
            "SELECT ts,o,h,l,c,v FROM candles WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT ?",
            (symbol, tf, limit),
        ).fetchall()
        return [
            Candle(datetime.fromtimestamp(ts / 1000, UTC), *map(Decimal, (o, h, low, c, v)))
            for ts, o, h, low, c, v in reversed(rows)
        ]
