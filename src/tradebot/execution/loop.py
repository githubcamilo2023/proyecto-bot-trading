"""Bucle de ejecución: un ciclo por vela, latido en disco y freno automático ante fallos repetidos."""

import json
import logging
import math
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradebot.alerts import Notify
from tradebot.audit import AuditError, AuditLog
from tradebot.config import MAX_TICK_FAILURES
from tradebot.execution.bot import Bot
from tradebot.risk import RiskEngine
from tradebot.timeframe import tf_seconds

log = logging.getLogger("tradebot.loop")
CANDLE_CLOSE_DELAY = 10  # segundos de margen tras el cierre de la vela para que el exchange la publique
MAX_BACKOFF = 300


def seconds_until_next_close(now: datetime, timeframe: str, delay: int = CANDLE_CLOSE_DELAY) -> float:
    step = tf_seconds(timeframe)
    ts = now.timestamp()
    return (math.floor(ts / step) + 1) * step + delay - ts


def write_heartbeat(
    path: Path, now: datetime, status: str, detail: str, failures: int, next_due: datetime
) -> None:
    """Latido atómico: `tradebot status` lo lee para saber si el bot sigue vivo."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "ts": now.isoformat(),
                    "status": status,
                    "detail": detail,
                    "failures": failures,
                    "next_due": next_due.isoformat(),
                }
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        log.exception("no se pudo escribir el latido")


def run_loop(
    bot: Bot,
    risk: RiskEngine,
    audit: AuditLog | None,
    heartbeat: Path,
    sleep: Callable[[float], object],
    stop: Callable[[], bool] = lambda: False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_failures: int = MAX_TICK_FAILURES,
    notify: Notify | None = None,
) -> int:
    """0 si termina de forma normal; 3 si se detuvo por fallos repetidos (con el kill switch activado)."""
    failures = 0
    tf = bot.strategy.timeframe
    while not stop():
        try:
            result = bot.tick()
        except Exception as e:  # noqa: BLE001 - el bucle no debe morir por un fallo puntual de red o datos
            failures += 1
            log.exception("fallo en el ciclo (%s/%s)", failures, max_failures)
            backoff = min(MAX_BACKOFF, 5 * 2**failures)
            t = now()
            write_heartbeat(
                heartbeat, t, "error", f"{type(e).__name__}: {e}", failures, t + timedelta(seconds=backoff)
            )
            if failures >= max_failures:
                risk.kill(f"{failures} fallos seguidos del bot", "bot")
                if audit is not None:
                    try:
                        audit.append(
                            "bot",
                            "bot.halt",
                            bot.strategy.name,
                            "circuit-breaker",
                            {"failures": str(failures)},
                        )
                    except AuditError:
                        log.exception("no se pudo auditar la parada")
                if notify:
                    notify(f"{bot.strategy.name}: el bot se DETUVO tras {failures} fallos (kill switch).")
                return 3
            sleep(backoff)
            continue
        failures = 0
        t = now()
        wait = seconds_until_next_close(t, tf)
        write_heartbeat(
            heartbeat, t, "ok", f"{result.action}: {result.reason}", 0, t + timedelta(seconds=wait)
        )
        sleep(wait)
    return 0
