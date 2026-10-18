"""Protecciones del motor de riesgo (idea de freqtrade): frenos por HISTORIAL, no por la estrategia.

- Espera tras salir: no vuelve a comprar hasta que pasen N velas completas desde la última venta.
- Pausa tras stop-loss: si saltan K stop-loss dentro de las últimas W velas, no compra durante P velas
  desde el último. Evita "seguir comprando" mientras el mercado castiga una y otra vez.
Solo frenan COMPRAS; vender y el stop-loss siempre siguen permitidos. Las usan el bot y el backtest por igual.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from tradebot.models import RiskLimits


@dataclass(frozen=True, slots=True)
class Exit:
    ts: datetime  # vela en la que se vendió
    stopped: bool  # salió por stop-loss


def buy_block(exits: Iterable[Exit], ts: datetime, tf_seconds: int, limits: RiskLimits) -> str | None:
    """Motivo por el que NO se debe comprar en la vela `ts`, o None si se puede."""
    tf = timedelta(seconds=tf_seconds)
    past = sorted((e for e in exits if e.ts <= ts), key=lambda e: e.ts)
    if not past:
        return None
    if limits.cooldown_candles and ts - past[-1].ts <= tf * limits.cooldown_candles:
        return f"espera tras salir ({limits.cooldown_candles} vela(s) completas)"
    n, window, pause = limits.stoploss_guard_count, limits.stoploss_guard_window, limits.stoploss_guard_pause
    if n:
        stops = [e for e in past if e.stopped and ts - e.ts <= tf * window]
        if len(stops) >= n and ts - stops[-1].ts <= tf * pause:
            return f"pausa tras {len(stops)} stop-loss en {window} velas (dura {pause} velas)"
    return None
