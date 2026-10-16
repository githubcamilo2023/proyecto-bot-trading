from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradebot.data import CachedMarketData, CandleCache, CcxtSource
from tradebot.models import Candle
from tradebot.timeframe import tf_seconds

T0 = datetime(2026, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)


def candle(i: int, tf: timedelta = H) -> Candle:
    return Candle(T0 + tf * i, D("1"), D("2"), D("0.5"), D("1.5"), D("10"))


def ms(i: int) -> int:
    return int((T0 + H * i).timestamp() * 1000)


class FakeSource:
    """Fuente falsa: devuelve velas 0..n-1 desde `since` y registra cada petición."""

    def __init__(self, n: int) -> None:
        self.n, self.calls = n, []

    def fetch(self, symbol, timeframe, since):
        self.calls.append(since)
        return [candle(i) for i in range(self.n) if T0 + H * i >= since]


class FakeExchange:
    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls += 1
        return [r for r in self.rows if r[0] >= since][:limit]


@pytest.fixture
def cache() -> Iterator[CandleCache]:
    with CandleCache() as c:  # se cierra siempre, pase lo que pase en el test
        yield c


@pytest.mark.parametrize("tf,sec", [("1m", 60), ("15m", 900), ("4h", 14400), ("1d", 86400), ("1w", 604800)])
def test_tf_seconds(tf, sec):
    assert tf_seconds(tf) == sec


@pytest.mark.parametrize("bad", ["", "h", "0h", "-1h", "4x", "1.5h"])
def test_tf_seconds_rejects_invalid(bad):
    with pytest.raises(ValueError):
        tf_seconds(bad)


def test_cache_roundtrip_keeps_decimal_precision(cache):
    c = Candle(T0, D("60000.12345678"), D("60100"), D("59900"), D("60050.5"), D("0.00012345"))
    cache.save("BTC/USDT", "1h", [c])
    assert cache.load("BTC/USDT", "1h", 10) == [c]


def test_cache_save_is_idempotent_and_load_returns_last_n_ascending(cache):
    cache.save("BTC/USDT", "1h", [candle(i) for i in range(5)])
    cache.save("BTC/USDT", "1h", [candle(i) for i in range(3)])
    assert cache.count("BTC/USDT", "1h") == 5
    assert [c.ts for c in cache.load("BTC/USDT", "1h", 2)] == [candle(3).ts, candle(4).ts]
    assert cache.last_ts("BTC/USDT", "1h") == candle(4).ts
    assert cache.last_ts("ETH/USDT", "1h") is None


def test_cached_market_data_fetches_only_what_is_missing(cache):
    src = FakeSource(5)
    md = CachedMarketData(src, cache, now=lambda: T0 + H * 5)
    assert len(md.candles("BTC/USDT", "1h", 5)) == 5
    src.n = 8  # el exchange cerró 3 velas nuevas
    out = md.candles("BTC/USDT", "1h", 5)
    assert src.calls[1] == candle(4).ts + H  # segunda petición: solo desde la última vela guardada
    assert [c.ts for c in out] == [candle(i).ts for i in range(3, 8)]


def test_cached_market_data_backfills_when_cache_has_fewer_than_limit(cache):
    src = FakeSource(10)
    md = CachedMarketData(src, cache, now=lambda: T0 + H * 10)
    md.candles("BTC/USDT", "1h", 3)
    assert len(md.candles("BTC/USDT", "1h", 8)) == 8


def test_ccxt_source_drops_the_open_candle_and_converts_to_decimal():
    rows = [[ms(i), 1.1, 2.2, 0.5, 1.5, 10.0] for i in range(4)]
    src = CcxtSource(exchange=FakeExchange(rows), now=lambda: T0 + H * 3 + timedelta(minutes=30))
    out = src.fetch("BTC/USDT", "1h", T0)
    assert [c.ts for c in out] == [candle(i).ts for i in range(3)]  # la vela 3 sigue abierta
    assert out[0].open == D("1.1")


def test_ccxt_source_paginates():
    rows = [[ms(i), 1, 2, 0.5, 1.5, 10] for i in range(7)]
    ex = FakeExchange(rows)
    src = CcxtSource(exchange=ex, now=lambda: T0 + H * 100, page=3)
    assert len(src.fetch("BTC/USDT", "1h", T0)) == 7
    assert ex.calls == 3
