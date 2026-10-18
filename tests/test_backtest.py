from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradebot.backtest import run
from tradebot.models import Candle, RiskLimits
from tradebot.strategy import Strategy

T0 = datetime(2026, 1, 1, tzinfo=UTC)
H = timedelta(hours=1)
CLOSE = {"kind": "close"}


def cmp(left, op, right) -> dict:
    return {"type": "cmp", "left": left, "op": op, "right": right}


ALWAYS, NEVER = cmp(CLOSE, ">", 0), cmp(CLOSE, "<", -1)


def strat(entry: dict, exit_: dict, size: str = "0.1", stop: str = "0.5") -> Strategy:
    """stop=0.5 por defecto: la mayoría de tests no prueban el stop-loss y no deben activarlo sin querer."""
    spec = {"name": "t", "symbol": "BTC/USDT", "timeframe": "1h", "size_pct": size, "stop_loss_pct": stop}
    return Strategy.model_validate(spec | {"entry": entry, "exit": exit_})


def bars(rows, step: timedelta = H) -> list[Candle]:
    """rows = [(apertura, cierre), ...]"""

    def one(i: int, o, c) -> Candle:
        o, c = D(str(o)), D(str(c))
        return Candle(T0 + step * i, o, max(o, c), min(o, c), c, D(1))

    return [one(i, o, c) for i, (o, c) in enumerate(rows)]


LIMITS = RiskLimits(max_order_pct=D("0.1"), max_daily_loss_pct=D("0.03"))
FREE = {"fee_pct": D(0), "slippage_pct": D(0)}
RISE = [(7, 7), (10, 10), (20, 20), (110, 110), (110, 110)]
EXIT_OVER_100 = cmp(CLOSE, ">", 100)


def test_signal_executes_at_next_open_not_same_close():
    c = bars(RISE)
    r = run(strat(ALWAYS, EXIT_OVER_100), c, LIMITS, D(10000), **FREE)
    t = r.trades[0]
    assert (t.entry_price, t.exit_price) == (D(10), D(110))  # y no 7, el cierre donde nació la señal
    assert (t.entry_ts, t.exit_ts) == (c[1].ts, c[4].ts)
    assert t.pnl == D(10000) and r.final_equity == D(20000) and r.total_return == D(1)
    assert not r.open_at_end and r.win_rate == D(1)


def test_fees_and_slippage_reduce_the_result():
    r = run(strat(ALWAYS, EXIT_OVER_100), bars(RISE), LIMITS, D(10000), D("0.001"), D("0.01"))
    assert r.fees > 0 and r.trades[0].pnl < D(10000) and r.final_equity < D(20000)


def test_risk_engine_rejects_oversized_orders():
    r = run(strat(ALWAYS, NEVER, size="0.5"), bars(RISE), LIMITS, D(10000), **FREE)
    assert not r.trades and r.final_equity == D(10000) and not r.open_at_end
    assert r.rejected["orden excede el tamaño máximo permitido"] > 0


DROP = [(10, 10), (10, 10), (10, 6), (6, 4), (4, 4), (4, 4)]
HALF = RiskLimits(max_order_pct=D(1), max_daily_loss_pct=D("0.03"), max_drawdown_pct=D(1))  # aísla la diaria
EXIT_UNDER_5 = cmp(CLOSE, "<", 5)


def test_daily_loss_limit_blocks_new_buys_and_drawdown_is_measured():
    r = run(strat(ALWAYS, EXIT_UNDER_5, size="0.5", stop="0.99"), bars(DROP), HALF, D(10000), **FREE)
    assert len(r.trades) == 1 and r.trades[0].pnl == D(-3000)
    assert r.rejected == {"pérdida diaria máxima alcanzada": 1}
    assert r.max_drawdown == D("0.3") and r.win_rate == D(0)


def test_max_drawdown_stops_new_buys_like_the_live_bot():
    limits = RiskLimits(max_order_pct=D(1), max_daily_loss_pct=D(1), max_drawdown_pct=D("0.10"))
    c = bars(DROP, step=timedelta(hours=5))  # días distintos: la pérdida diaria no interviene
    r = run(strat(ALWAYS, EXIT_UNDER_5, size="0.5", stop="0.99"), c, limits, D(10000), **FREE)
    assert len(r.trades) == 1 and not r.open_at_end  # tras caer 30 % ya no vuelve a comprar
    assert r.rejected == {"kill switch activo": 1}
    assert "caída máxima" in r.summary()


def test_new_utc_day_resets_the_daily_loss_measure():
    c = bars(DROP, step=timedelta(hours=5))
    r = run(strat(ALWAYS, EXIT_UNDER_5, size="0.5", stop="0.99"), c, HALF, D(10000), **FREE)
    assert not r.rejected and r.open_at_end and len(r.trades) == 1  # la recompra del día siguiente sí pasa


def test_open_position_is_marked_to_last_close_and_buy_hold_is_reported():
    r = run(strat(ALWAYS, NEVER), bars([(10, 10), (10, 10), (10, 12)]), LIMITS, D(10000), **FREE)
    assert r.open_at_end and not r.trades and r.final_equity == D(10200)
    assert r.total_return == D("0.02") and r.buy_hold_return == D("0.2")


def test_summary_is_readable_and_warns_about_small_samples():
    text = run(strat(ALWAYS, EXIT_OVER_100), bars(RISE), LIMITS, D(10000), **FREE).summary()
    assert "Estrategia: t v1 · BTC/USDT 1h" in text
    assert "Operaciones cerradas: 1" in text and "100.0%" in text and "+100.00%" in text
    assert "menos de 30 operaciones" in text


def test_needs_at_least_two_candles():
    with pytest.raises(ValueError):
        run(strat(ALWAYS, NEVER), bars([(10, 10)]))


# --- stop-loss -----------------------------------------------------------------------------------


def _ohlc(rows, step: timedelta = H) -> list[Candle]:
    """rows = [(apertura, máximo, mínimo, cierre), ...]"""
    return [
        Candle(T0 + step * i, D(str(o)), D(str(h)), D(str(lo)), D(str(c)), D(1))
        for i, (o, h, lo, c) in enumerate(rows)
    ]


def test_default_stop_loss_is_three_percent():
    assert strat(ALWAYS, NEVER).model_copy().stop_loss_pct == D("0.5")
    spec = {"name": "t", "symbol": "BTC/USDT", "timeframe": "1h", "size_pct": "0.1"}
    s = Strategy.model_validate(spec | {"entry": ALWAYS, "exit": NEVER})
    assert s.stop_loss_pct == D("0.03")


@pytest.mark.parametrize("bad", ["0", "-0.01", "1", "1.5"])
def test_invalid_stop_loss_is_rejected(bad):
    with pytest.raises(ValueError):
        strat(ALWAYS, NEVER, stop=bad)


def test_stop_loss_exits_at_the_stop_price_when_the_candle_touches_it():
    # entra a 100 (apertura de la vela 1); la vela 2 baja hasta 98 -> stop al 1 % = 99
    c = _ohlc([(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 98, 99), (99, 99, 99, 99)])
    r = run(strat(ALWAYS, NEVER, stop="0.01"), c, LIMITS, D(10000), **FREE)
    t = r.trades[0]
    assert t.stopped and t.exit_price == D(99) and t.exit_ts == c[2].ts
    assert t.pnl == (D(99) - D(100)) * t.qty
    assert "Salidas por stop-loss: 1" in r.summary()


def test_stop_loss_on_a_gap_down_exits_at_the_open_not_at_the_stop():
    c = _ohlc([(100, 100, 100, 100), (100, 100, 100, 100), (95, 95, 94, 94)])  # abre 5 % abajo
    r = run(strat(ALWAYS, NEVER, stop="0.01"), c, LIMITS, D(10000), **FREE)
    assert r.trades[0].stopped and r.trades[0].exit_price == D(
        95
    )  # peor que el stop: así pasa en la realidad


def test_stop_loss_does_not_trigger_if_price_stays_above_it():
    c = _ohlc([(100, 100, 100, 100), (100, 100, 100, 100), (100, 101, 99.5, 100)])
    r = run(strat(ALWAYS, NEVER, stop="0.01"), c, LIMITS, D(10000), **FREE)
    assert not r.trades and r.open_at_end


def test_stop_loss_can_trigger_on_the_entry_candle_itself():
    c = _ohlc([(100, 100, 100, 100), (100, 100, 98, 98), (98, 98, 98, 98)])  # entra a 100 y esa vela cae a 98
    r = run(strat(ALWAYS, NEVER, stop="0.01"), c, LIMITS, D(10000), **FREE)
    assert r.trades[0].stopped and r.trades[0].entry_ts == r.trades[0].exit_ts


def test_stop_loss_includes_costs():
    c = _ohlc([(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 98, 99)])
    r = run(strat(ALWAYS, NEVER, stop="0.01"), c, LIMITS, D(10000), D("0.001"), D("0.0005"))
    t = r.trades[0]
    assert t.stopped and t.exit_price < D(99) and t.entry_price > D(100) and t.pnl < 0 and r.fees > 0
