"""Validación de velas antes de operar: un dato malo no debe producir una orden."""

from datetime import datetime, timedelta

from tradebot.config import MAX_CANDLE_JUMP, MAX_STALE_CANDLES
from tradebot.models import Candle
from tradebot.timeframe import tf_seconds


def check_candles(candles: list[Candle], timeframe: str, now: datetime, needed: int) -> str | None:
    """None si los datos son operables; si no, el motivo en español (y el bot no opera ese ciclo).

    Se revisan las últimas `needed` velas: suficientes, ordenadas, sin huecos, con precios coherentes,
    sin saltos absurdos y con la última lo bastante reciente.
    """
    step = timedelta(seconds=tf_seconds(timeframe))
    if len(candles) < needed:
        return f"datos insuficientes: {len(candles)} velas, se necesitan {needed}"
    recent = candles[-needed:]
    for prev, cur in zip(recent, recent[1:], strict=False):
        if cur.ts - prev.ts != step:
            return f"hueco o desorden en las velas entre {prev.ts:%Y-%m-%d %H:%M} y {cur.ts:%Y-%m-%d %H:%M}"
        if prev.close > 0 and abs(cur.close / prev.close - 1) > MAX_CANDLE_JUMP:
            return f"salto de precio sospechoso en la vela {cur.ts:%Y-%m-%d %H:%M}"
    for c in recent:
        ok = (
            min(c.open, c.high, c.low, c.close) > 0
            and c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
        )
        if not ok:
            return f"vela incoherente en {c.ts:%Y-%m-%d %H:%M} (precios fuera de rango)"
    closes_at = recent[-1].ts + step  # la vela cierra un paso después de su apertura
    if now - closes_at > step * MAX_STALE_CANDLES:
        return f"datos viejos: la última vela cerró {closes_at:%Y-%m-%d %H:%M} UTC"
    return None
