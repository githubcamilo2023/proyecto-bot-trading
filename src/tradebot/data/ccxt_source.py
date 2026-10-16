from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tradebot.models import Candle
from tradebot.timeframe import tf_seconds


class CcxtSource:
    """Velas públicas vía CCXT (sin cuenta). Descarta la vela en curso: solo velas cerradas."""

    def __init__(
        self,
        exchange_id: str = "binance",
        exchange: Any = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        page: int = 500,
    ) -> None:
        if exchange is None:
            import ccxt  # import diferido: los tests no lo necesitan

            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        self.ex, self.now, self.page = exchange, now, page

    def fetch(self, symbol: str, timeframe: str, since: datetime) -> list[Candle]:
        step_ms = tf_seconds(timeframe) * 1000
        cursor = int(since.timestamp() * 1000)
        rows: dict[int, list[Any]] = {}
        while batch := self.ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=self.page):
            rows |= {r[0]: r for r in batch}
            nxt = batch[-1][0] + step_ms
            if len(batch) < self.page or nxt <= cursor:
                break
            cursor = nxt
        cutoff = self.now() - timedelta(milliseconds=step_ms)
        return [
            Candle(datetime.fromtimestamp(ts / 1000, UTC), *(Decimal(str(x)) for x in r[1:6]))
            for ts, r in sorted(rows.items())
            if datetime.fromtimestamp(ts / 1000, UTC) <= cutoff
        ]
