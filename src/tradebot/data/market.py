from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from tradebot.data.cache import CandleCache
from tradebot.models import Candle
from tradebot.ports import CandleSource
from tradebot.timeframe import tf_seconds


class CachedMarketData:
    """MarketData que pide a la fuente solo lo que falta y sirve el resto desde la caché local."""

    def __init__(
        self,
        source: CandleSource,
        cache: CandleCache,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.source, self.cache, self.now = source, cache, now

    def candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        step = timedelta(seconds=tf_seconds(timeframe))
        last = self.cache.last_ts(symbol, timeframe)
        enough = self.cache.count(symbol, timeframe) >= limit
        since = last + step if last is not None and enough else self.now() - step * limit
        if new := self.source.fetch(symbol, timeframe, since):
            self.cache.save(symbol, timeframe, new)
        return self.cache.load(symbol, timeframe, limit)
