from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradebot.backtest.engine import Result, Trade
from tradebot.backtest.evaluate import MIN_TRADES, Evaluation, Segment, evaluate
from tradebot.models import Candle
from tradebot.strategy import Strategy

T0 = datetime(2026, 1, 1, tzinfo=UTC)
SPEC = {
    "name": "onda",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "size_pct": "0.02",
    "entry": {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": "<", "right": 30},
    "exit": {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": ">", "right": 55},
}


def trade(pnl: str) -> Trade:
    return Trade(T0, T0, D(1), D(1), D(1), D(100), D(pnl))


def result(final: str, trades: tuple[Trade, ...] = (), hold: str = "0.01") -> Result:
    curve = ((T0, D(100)), (T0 + timedelta(hours=1), D(final)))
    return Result("x", D(100), D(final), trades, curve, D(0), {}, False, D(hold))


def evaluation(finals: list[str], n_trades: int) -> Evaluation:
    segs = [Segment(T0, T0, result(f, tuple(trade("1") for _ in range(n_trades)))) for f in finals]
    return Evaluation("x v1", tuple(segs))


def candles(n: int) -> list[Candle]:
    out = []
    for i in range(n):
        p = D(100 + (i % 40) - 20)
        out.append(Candle(T0 + timedelta(hours=i), p, p + 1, p - 1, p, D(1)))
    return out


def test_profit_factor():
    assert result("100", (trade("30"), trade("-10"), trade("-5"))).profit_factor == D(2)
    assert result("100", (trade("30"),)).profit_factor is None  # sin pérdidas: no se puede dividir
    assert result("100").profit_factor is None


def test_verdict_small_sample():
    text = evaluation(["110", "110"], 1).render()
    assert "Muestra pequeña (2 < 30" in text and "no se puede concluir nada" in text


def test_verdict_inconsistent_when_some_segment_loses():
    text = evaluation(["110", "90"], MIN_TRADES).render()
    assert "Tramos con ganancia: 1 de 2" in text and "No es consistente" in text


def test_verdict_consistent_never_promises_future():
    text = evaluation(["110", "105"], MIN_TRADES).render()
    assert "Fue consistente" in text and "no garantiza el futuro" in text


def test_counters():
    ev = evaluation(["110", "90"], 1)
    assert ev.positive == 1 and ev.beat_hold == 1 and ev.trades == 2
    assert ev.worst_drawdown == D("0.1")


def test_evaluate_splits_history_into_consecutive_segments():
    ev = evaluate(Strategy.model_validate(SPEC), candles(400), segments=4)
    assert len(ev.segments) == 4
    starts = [g.start for g in ev.segments]
    assert starts == sorted(starts) and ev.segments[0].start == T0
    assert ev.segments[-1].end == T0 + timedelta(hours=399)


def test_evaluate_rejects_bad_arguments():
    s = Strategy.model_validate(SPEC)
    with pytest.raises(ValueError, match="al menos 2"):
        evaluate(s, candles(400), segments=1)
    with pytest.raises(ValueError, match="muy poca historia"):
        evaluate(s, candles(100), segments=4)


# --- verificaciones ------------------------------------------------------------------------------------

from tradebot.backtest.evaluate import Checks, scaled, window_consistency  # noqa: E402

CLOSE = {"kind": "close"}


def test_scaled_multiplies_every_period_and_keeps_a_minimum_of_two():
    s = Strategy.model_validate(
        {
            **SPEC,
            "entry": {"type": "all", "of": [
                {"type": "cmp", "left": {"kind": "rsi", "period": 2}, "op": "<", "right": 10},
                {"type": "cmp", "left": CLOSE, "op": ">", "right": {"kind": "sma", "period": 200}},
            ]},
        }
    )  # fmt: skip
    up, down = scaled(s, D("1.2")), scaled(s, D("0.8"))
    assert up.entry.of[1].right.period == 240 and up.exit.left.period == 17  # type: ignore[union-attr]
    assert down.entry.of[0].left.period == 2  # 2 x 0.8 = 1.6 -> mínimo 2  # type: ignore[union-attr]


def test_window_consistency_needs_enough_history_and_compares_decisions():
    s = Strategy.model_validate(SPEC)
    assert window_consistency(s, candles(400)) == (0, 0)  # no alcanza para el doble de ventana
    checked, diff = window_consistency(s, candles(1500), sample=50)
    assert checked == 100 and diff >= 0


def test_checks_render_flags_fragile_and_window_problems():
    ok = Checks(10, 0, (("base", D("0.1")), ("−20 %", D("0.05")), ("+20 %", D("0.02"))))
    text = "\n".join(ok.render())
    assert "Ventana: OK" in text and "estable" in text and "Datos futuros: imposibles por diseño" in text
    bad = Checks(10, 3, (("base", D("0.1")), ("−20 %", D("-0.05")), ("+20 %", D("0.02"))))
    text = "\n".join(bad.render())
    assert "ATENCIÓN. 3 de 10" in text and "FRÁGIL" in text
    assert "no hay historia suficiente" in "\n".join(Checks(0, 0, ok.stability).render())


def test_evaluate_includes_checks_unless_disabled():
    s = Strategy.model_validate(SPEC)
    assert "VERIFICACIONES" in evaluate(s, candles(400)).render()
    assert "VERIFICACIONES" not in evaluate(s, candles(400), verify=False).render()
