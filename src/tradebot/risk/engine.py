import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from tradebot.audit import AuditError, AuditLog
from tradebot.models import Order, RiskLimits, Side

log = logging.getLogger("tradebot.risk")


def _atomic_write(path: Path, text: str) -> None:
    """Escribe vía un temporal propio de ESTE archivo y ESTE proceso: dos consolas a la vez no se pisan."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)  # atómico


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str


class RiskEngine:
    """Toda orden pasa por aquí. Spot only: las ventas reducen exposición y siempre se permiten.

    Kill switch persistente: con `state_path`, el estado vive en disco y se relee en cada orden.
    Así sobrevive a un reinicio y se puede activar desde OTRO proceso (`tradebot kill`).
    Si el archivo existe pero no se puede leer, se asume ACTIVADO (falla cerrado).
    """

    def __init__(
        self,
        limits: RiskLimits,
        equity: Decimal,
        state_path: str | Path | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.limits, self._equity, self._audit = limits, equity, audit
        self._state = Path(state_path) if state_path else None
        self._killed = False
        self._peak = equity  # máximo de capital visto; con state_path se guarda en <state>.peak
        self._peak_path = self._state.with_suffix(".peak") if self._state else None

    @property
    def equity(self) -> Decimal:
        return self._equity

    def set_equity(self, equity: Decimal) -> None:
        self._equity = equity

    @property
    def killed(self) -> bool:
        return self._killed or self.killed_on_disk()

    def killed_on_disk(self) -> bool:
        """True si el archivo de estado dice activado (o no se puede leer). Sin archivo configurado: False."""
        if self._state is None:
            return False
        try:
            data = json.loads(self._state.read_text(encoding="utf-8"))
            return bool(data["killed"])
        except FileNotFoundError:
            return False
        except (OSError, ValueError, KeyError, TypeError):
            log.error("estado del kill switch ilegible en %s: se asume ACTIVADO", self._state)
            return True

    def kill_reason(self) -> str:
        """Motivo guardado del kill switch activo ('' si no está activo o no consta). Solo lectura."""
        if not self.killed:
            return ""
        try:
            data = json.loads(self._state.read_text(encoding="utf-8")) if self._state else {}
            return str(data.get("reason", ""))[:200] if isinstance(data, dict) else ""
        except (OSError, ValueError):
            return "estado ilegible"

    def _write_state(self, killed: bool, reason: str, actor: str) -> None:
        if self._state is None:
            return
        self._state.parent.mkdir(parents=True, exist_ok=True)
        record = {"killed": killed, "reason": reason, "actor": actor, "at": datetime.now(UTC).isoformat()}
        _atomic_write(self._state, json.dumps(record))

    def _load_peak(self) -> Decimal | None:
        if self._peak_path is None:
            return self._peak
        try:
            return Decimal(self._peak_path.read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            return None
        except (OSError, ValueError, ArithmeticError):
            log.error("máximo de capital ilegible en %s: se reinicia con el capital actual", self._peak_path)
            return None

    def observe(self, equity: Decimal) -> bool:
        """Registra el capital. Si cae más de `max_drawdown_pct` desde su máximo, activa el kill switch
        (bloquea compras; vender sigue permitido). True solo si lo activó en esta llamada."""
        peak = max(self._load_peak() or equity, equity)
        if peak != self._peak or (self._peak_path and not self._peak_path.exists()):
            self._peak = peak
            if self._peak_path is not None:
                try:
                    self._peak_path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(self._peak_path, str(peak))
                except OSError:
                    log.exception("no se pudo guardar el máximo de capital")
        dd = self.limits.max_drawdown_pct
        if self.killed or equity > peak * (1 - dd):
            return False
        self.kill(
            f"caída máxima del {dd:.0%} desde el máximo de capital ({peak:,.2f} → {equity:,.2f})",
            "risk-engine",
        )
        return True

    def kill(self, reason: str = "manual", actor: str = "system") -> bool:
        """Activa el kill switch. True si quedó persistido (único caso en que frena a OTROS procesos).
        Si se persistió, manda el disco: así `tradebot release` desde otra consola también libera a este
        proceso. Si no se pudo escribir (o no hay archivo), queda activado en memoria y no se puede perder."""
        log.warning("kill switch activado por %s: %s", actor, reason)
        self._try_audit(actor, "killswitch.activate", "global", "ok", {"reason": reason})
        if self._state is None:
            self._killed = True
            return False
        try:
            self._write_state(True, reason, actor)
        except OSError:
            self._killed = True
            log.exception("no se pudo persistir el kill switch (sigue activo en memoria)")
            return False
        return True

    def release(self, actor: str) -> None:
        """Desactiva el kill switch. Solo si se puede persistir; si falla, sigue activado y lanza OSError."""
        if self._audit is not None:  # reabrir el trading exige dejar rastro: si no, no se libera
            self._audit.append(actor, "killswitch.release", "global", "ok", {})
        self._write_state(False, "liberado", actor)
        self._killed = False
        self._peak = self._equity  # tras liberar, la caída se mide desde el capital de ese momento
        if self._peak_path is not None:
            self._peak_path.unlink(missing_ok=True)
        log.warning("kill switch liberado por %s", actor)

    def check(self, order: Order, open_positions: int, daily_pnl: Decimal) -> Decision:
        d = self._evaluate(order, open_positions, daily_pnl)
        if not self._try_audit(
            "risk-engine",
            "order.check",
            f"{order.symbol} {order.side}",
            "allow" if d.allowed else "deny",
            {"qty": str(order.qty), "price": str(order.price), "reason": d.reason},
        ) and (order.side is Side.BUY and d.allowed):
            d = Decision(False, "auditoría no disponible")  # sin rastro no se abre exposición nueva
        log.info(
            "riesgo %s %s px=%s qty=%s -> %s (%s)",
            order.symbol,
            order.side,
            order.price,
            order.qty,
            d.allowed,
            d.reason,
        )
        return d

    def _try_audit(self, actor: str, action: str, subject: str, result: str, details: dict[str, str]) -> bool:
        """False solo si hay auditoría configurada y falló al escribir."""
        if self._audit is None:
            return True
        try:
            self._audit.append(actor, action, subject, result, details)
        except AuditError:
            log.exception("fallo de auditoría en %s", action)
            return False
        return True

    def _evaluate(self, o: Order, open_positions: int, daily_pnl: Decimal) -> Decision:
        if o.qty <= 0 or o.price <= 0:
            return Decision(False, "cantidad o precio inválidos")
        if o.side is Side.SELL:
            return Decision(True, "venta: reduce exposición")
        lim = self.limits
        checks = (
            (self.killed, "kill switch activo"),
            (daily_pnl <= -self._equity * lim.max_daily_loss_pct, "pérdida diaria máxima alcanzada"),
            (open_positions >= lim.max_open_positions, "máximo de posiciones abiertas"),
            (o.notional > self._equity * lim.max_order_pct, "orden excede el tamaño máximo permitido"),
        )
        return next((Decision(False, why) for hit, why in checks if hit), Decision(True, "ok"))
