import operator
from decimal import Decimal
from enum import StrEnum

from tradebot.models import Candle
from tradebot.strategy import indicators as ind
from tradebot.strategy.rules import AllOf, AnyOf, Close, Cmp, Cond, Moving, Operand, Strategy


class Signal(StrEnum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


_FN = {"sma": ind.sma, "ema": ind.ema, "rsi": ind.rsi}
_CMP = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def _value(op: Operand, closes: list[Decimal]) -> Decimal | None:
    match op:
        case Close():
            return closes[-1] if closes else None
        case Moving():
            return _FN[op.kind](closes, op.period)
        case _:
            return op


def _eval(c: Cond, closes: list[Decimal]) -> bool:
    match c:
        case AllOf():
            return all(_eval(x, closes) for x in c.of)
        case AnyOf():
            return any(_eval(x, closes) for x in c.of)
        case Cmp(op="crosses_above" | "crosses_below"):
            prev, now = closes[:-1], closes
            a0, b0, a1, b1 = (_value(o, s) for s in (prev, now) for o in (c.left, c.right))
            if a0 is None or b0 is None or a1 is None or b1 is None:
                return False
            return (a0 <= b0 and a1 > b1) if c.op == "crosses_above" else (a0 >= b0 and a1 < b1)
        case Cmp():
            a, b = _value(c.left, closes), _value(c.right, closes)
            return a is not None and b is not None and _CMP[c.op](a, b)
    return False


def _periods(c: Cond) -> list[int]:
    match c:
        case AllOf() | AnyOf():
            return [p for x in c.of for p in _periods(x)]
        case Cmp():
            return [o.period for o in (c.left, c.right) if isinstance(o, Moving)]
    return []


def min_candles(strategy: Strategy) -> int:
    """Velas mínimas para que todo indicador y todo cruce tenga valor: el periodo mayor + 2."""
    return max([*_periods(strategy.entry), *_periods(strategy.exit), 0]) + 2


def signal(strategy: Strategy, candles: list[Candle], in_position: bool) -> Signal:
    """Evalúa sobre velas CERRADAS. Sin posición solo mira `entry`; con posición solo mira `exit`.
    Si faltan datos para algún indicador, la condición es falsa (HOLD)."""
    closes = [c.close for c in candles]
    if in_position:
        return Signal.SELL if _eval(strategy.exit, closes) else Signal.HOLD
    return Signal.BUY if _eval(strategy.entry, closes) else Signal.HOLD
