from datetime import datetime
from decimal import Decimal
from typing import Protocol

from tradebot.models import Candle, Fill, Order, Position


class CandleSource(Protocol):
    def fetch(self, symbol: str, timeframe: str, since: datetime) -> list[Candle]:
        """Velas CERRADAS desde `since` (inclusive), en orden ascendente."""
        ...


class MarketData(Protocol):
    def candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]: ...


class Broker(Protocol):
    """Lo que el bot necesita de un broker. Hoy lo implementa `PaperBroker`; uno real igual."""

    fee_pct: Decimal
    slippage_pct: Decimal
    cash: Decimal
    position: Position | None

    def equity(self, mark_price: Decimal) -> Decimal: ...

    def has_fill(self, client_id: str) -> bool: ...

    def day_start_equity(self, now: datetime, mark_price: Decimal) -> Decimal: ...

    def submit(self, order: Order, client_id: str, entry_candle_ts: datetime | None = None) -> Fill:
        """Idempotente: el mismo `client_id` nunca ejecuta dos veces."""
        ...
