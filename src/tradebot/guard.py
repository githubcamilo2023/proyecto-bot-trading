"""Vigilante de SOLO LECTURA: avisa si el bot deja de latir o si se activa el kill switch.

Corre en su propia ventana. Cada `interval` segundos lee el latido y el kill switch y avisa UNA vez por
cada cambio de estado (problema y recuperación), sin repetir correos en cada vuelta.
Límite: si el PC se apaga, el vigilante se apaga con él; si se suspende, avisará al despertar.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tradebot.alerts import Notify
from tradebot.models import RiskLimits
from tradebot.risk import RiskEngine
from tradebot.watch import LATE_GRACE, Heartbeat, read_heartbeat

STARTUP_GRACE = timedelta(minutes=5)  # al arrancar, el bot tarda en escribir su primer latido


def health(hb: Heartbeat | None | str, kill_reason: str | None, now: datetime, since: datetime) -> str:
    """'' si todo bien; si no, el problema en una frase."""
    if kill_reason is not None:
        return f"kill switch ACTIVADO ({kill_reason or 'sin motivo guardado'}): el bot no compra"
    if isinstance(hb, Heartbeat):
        if now > hb.next_due + LATE_GRACE:
            mins = int((now - hb.next_due).total_seconds() // 60)
            return f"el bot NO late desde hace {mins} min (¿ventana cerrada, error o PC suspendido?)"
        return ""
    if hb is None:
        return "" if now - since < STARTUP_GRACE else "no hay latido: el bot no está corriendo"
    return f"latido {hb}"


def run_guard(
    notify: Notify,
    out: Callable[[str], object],
    *,
    heartbeat: str | Path,
    kill_state: str | Path,
    interval: float,
    sleep: Callable[[float], object],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    stop: Callable[[], bool] = lambda: False,
) -> int:
    since, last = now(), ""
    out("Vigilante en marcha (solo lectura). Avisará si el bot deja de latir o se activa el kill switch.")
    while not stop():
        sleep(interval)  # primero espera: al arrancar junto al bot, le da tiempo a escribir su latido
        risk = RiskEngine(RiskLimits(), Decimal(0), kill_state)
        problem = health(read_heartbeat(heartbeat), risk.kill_reason() if risk.killed else None, now(), since)
        if problem != last:
            msg = f"PROBLEMA: {problem}" if problem else "RECUPERADO: el bot vuelve a latir con normalidad"
            notify(msg)
            out(f"{now():%H:%M} {msg}")
            last = problem
    return 0
