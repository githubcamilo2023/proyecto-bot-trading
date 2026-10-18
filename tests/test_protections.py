import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradebot.backtest import run
from tradebot.models import Candle, RiskLimits
from tradebot.risk.protections import Exit, buy_block
from tradebot.strategy import Strategy

T0 = datetime(2026, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)
TF = 3600


def test_no_history_never_blocks():
    assert buy_block([], T0, TF, RiskLimits()) is None


def test_cooldown_waits_full_candles_after_an_exit():
    lim = RiskLimits(cooldown_candles=2, stoploss_guard_count=0)
    exits = [Exit(T0, stopped=False)]
    assert "espera tras salir" in (buy_block(exits, T0 + H, TF, lim) or "")
    assert "espera tras salir" in (buy_block(exits, T0 + 2 * H, TF, lim) or "")
    assert buy_block(exits, T0 + 3 * H, TF, lim) is None
    assert buy_block(exits, T0 - H, TF, lim) is None  # una venta "futura" no cuenta


def test_stoploss_guard_pauses_after_repeated_stops():
    lim = RiskLimits(
        cooldown_candles=0, stoploss_guard_count=3, stoploss_guard_window=24, stoploss_guard_pause=12
    )
    stops = [Exit(T0 + i * 2 * H, stopped=True) for i in range(3)]  # 3 stops en 4 h
    last = stops[-1].ts
    assert "pausa tras 3 stop-loss" in (buy_block(stops, last + 5 * H, TF, lim) or "")
    assert buy_block(stops, last + 13 * H, TF, lim) is None  # terminó la pausa
    assert buy_block(stops[:2], last + H, TF, lim) is None  # 2 no bastan
    normal = [Exit(e.ts, stopped=False) for e in stops]
    assert buy_block(normal, last + H, TF, lim) is None  # ventas normales no cuentan
    spread = [Exit(T0 + i * 20 * H, stopped=True) for i in range(3)]  # fuera de la ventana de 24
    assert buy_block(spread, spread[-1].ts + H, TF, lim) is None


def test_protections_can_be_disabled_and_are_validated():
    off = RiskLimits(cooldown_candles=0, stoploss_guard_count=0)
    assert buy_block([Exit(T0, True)] * 5, T0 + H, TF, off) is None
    with pytest.raises(ValueError):
        RiskLimits(cooldown_candles=-1)
    with pytest.raises(ValueError):
        RiskLimits(stoploss_guard_pause=5000)


def candles(rows):
    return [Candle(T0 + H * i, D(o), D(max(o, c)), D(min(o, c)), D(c), D(1)) for i, (o, c) in enumerate(rows)]


SPEC = {
    "name": "t", "symbol": "BTC/USDT", "timeframe": "1h", "size_pct": "0.5", "stop_loss_pct": "0.5",
    "entry": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 0},
    "exit": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 105},
}  # fmt: skip


def test_backtest_applies_the_same_cooldown_as_the_bot():
    rows = [(100, 100), (100, 110), (100, 100), (100, 110), (100, 100), (100, 100)]
    s = Strategy.model_validate(SPEC)
    free = {"fee_pct": D(0), "slippage_pct": D(0)}
    base = RiskLimits(max_order_pct=D(1), max_daily_loss_pct=D(1), stoploss_guard_count=0)
    eager = run(s, candles(rows), dataclasses.replace(base, cooldown_candles=0), D(1000), **free)
    patient = run(s, candles(rows), base, D(1000), **free)
    assert len(patient.trades) < len(eager.trades)
    assert any("espera tras salir" in why for why in patient.rejected)
