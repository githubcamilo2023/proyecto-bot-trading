"""Un ciclo de decisión del bot (`tick`). Todo pasa por el motor de riesgo y deja rastro de auditoría.

Orden de decisión en cada ciclo:
1. Validar datos (huecos, retraso, precios absurdos): si no son fiables, no se opera.
2. Si hay posición abierta, comprobar el stop-loss con las velas posteriores a la entrada.
3. Comprobar que la estrategia sigue aprobada; si no, solo se permiten salidas por stop-loss.
4. Evaluar la señal sobre velas cerradas y, si procede, enviar la orden con un id determinista
   (estrategia + versión + vela): reintentar o reiniciar sobre la misma vela no duplica nada.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

from tradebot.audit import AuditError, AuditLog
from tradebot.config import MIN_WINDOW, QTY_STEP
from tradebot.data.validate import check_candles
from tradebot.execution.paper import BrokerError
from tradebot.models import Fill, Order, Position, Side
from tradebot.ports import Broker, MarketData
from tradebot.risk import RiskEngine
from tradebot.strategy import Signal, Strategy, signal
from tradebot.strategy.engine import min_candles
from tradebot.strategy.store import ApprovalError

log = logging.getLogger("tradebot.bot")


@dataclass(frozen=True, slots=True)
class TickResult:
    action: str  # hold | buy | sell | stop | skip | rejected
    reason: str


class Bot:
    def __init__(
        self,
        strategy: Strategy,
        market: MarketData,
        broker: Broker,
        risk: RiskEngine,
        audit: AuditLog | None = None,
        approval_check: Callable[[], object] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.strategy, self.market, self.broker, self.risk = strategy, market, broker, risk
        self.audit, self.approval_check, self.now = audit, approval_check, now
        self._tamper_reported = False

    # --- auditoría y aprobación -------------------------------------------------------------

    def _record(self, action: str, subject: str, result: str, **details: Any) -> None:
        if self.audit is None:
            return
        try:
            self.audit.append("bot", action, subject, result, {k: str(v) for k, v in details.items()})
        except AuditError:
            log.exception("fallo de auditoría en %s", action)

    def _approved(self) -> bool:
        if self.approval_check is None:
            return True
        try:
            self.approval_check()
        except ApprovalError as e:
            log.error("la estrategia ya no tiene aprobación válida: %s", e)
            if not self._tamper_reported:
                self._tamper_reported = True
                self._record("strategy.tamper_suspected", self.strategy.name, "denied", reason=e)
            return False
        return True

    # --- ciclo ------------------------------------------------------------------------------

    def tick(self) -> TickResult:
        s, now = self.strategy, self.now()
        warm = min_candles(s)
        window = max(MIN_WINDOW, 3 * warm)
        candles = self.market.candles(s.symbol, s.timeframe, window)
        if problem := check_candles(candles, s.timeframe, now, warm):
            log.warning("sin operar: %s", problem)
            self._record("bot.skip", s.name, "skip", reason=problem)
            return TickResult("skip", problem)
        last, pos = candles[-1], self.broker.position
        if pos is not None and (stopped := self._check_stop(pos, candles, now)):
            return stopped
        if not self._approved():
            return TickResult("hold", "estrategia sin aprobación válida: solo se permite el stop-loss")
        sig = signal(s, candles, in_position=pos is not None)
        if sig is Signal.HOLD:
            return TickResult("hold", "sin señal")
        if sig is Signal.SELL and pos is not None:
            cid = f"{s.name}-v{s.version}-sell-{last.ts:%Y%m%dT%H%M}"
            return self._exit(pos, last.close, last.ts, cid, "sell", now)
        return self._enter(last.close, last.ts, now)

    def _check_stop(self, pos: Position, candles: list[Any], now: datetime) -> TickResult | None:
        s = self.strategy
        stop = pos.entry_price * (1 - s.stop_loss_pct)
        for c in candles:
            if c.ts > pos.entry_candle_ts and c.low <= stop:
                cid = f"{s.name}-v{s.version}-stop-{pos.entry_candle_ts:%Y%m%dT%H%M}"
                return self._exit(pos, min(stop, c.open), c.ts, cid, "stop", now)
        return None

    def _day_pnl(self, mark: Decimal, now: datetime) -> Decimal:
        equity = self.broker.equity(mark)
        self.risk.set_equity(equity)
        return equity - self.broker.day_start_equity(now, mark)

    def _exit(
        self, pos: Position, ref: Decimal, ts: datetime, cid: str, kind: str, now: datetime
    ) -> TickResult:
        if self.broker.has_fill(cid):
            return TickResult("hold", "orden ya ejecutada")
        order = Order(self.strategy.symbol, Side.SELL, pos.qty, ref, ts)
        decision = self.risk.check(order, 1, self._day_pnl(ref, now))
        if not decision.allowed:
            return TickResult("rejected", decision.reason)
        return self._send(order, cid, kind, None)

    def _enter(self, ref: Decimal, ts: datetime, now: datetime) -> TickResult:
        s = self.strategy
        cid = f"{s.name}-v{s.version}-buy-{ts:%Y%m%dT%H%M}"
        if self.broker.has_fill(cid):
            return TickResult("hold", "la señal de esta vela ya se ejecutó")
        day_pnl = self._day_pnl(ref, now)
        px = ref * (1 + self.broker.slippage_pct)
        notional = min(self.risk.equity * s.size_pct, self.broker.cash / (1 + self.broker.fee_pct))
        qty = (notional / px).quantize(QTY_STEP, ROUND_DOWN)
        decision = self.risk.check(Order(s.symbol, Side.BUY, qty, px, ts), 0, day_pnl)
        if not decision.allowed:
            return TickResult("rejected", decision.reason)
        return self._send(Order(s.symbol, Side.BUY, qty, ref, ts), cid, "buy", ts)

    def _send(self, order: Order, cid: str, kind: str, entry_ts: datetime | None) -> TickResult:
        try:
            fill: Fill = self.broker.submit(order, cid, entry_candle_ts=entry_ts)
        except BrokerError as e:
            log.error("el broker rechazó %s: %s", cid, e)
            self._record("order.fill", cid, "error", reason=e)
            return TickResult("rejected", str(e))
        self._record(
            "order.fill", cid, "ok", kind=kind, side=fill.side, qty=fill.qty, price=fill.price, pnl=fill.pnl
        )
        log.info("%s ejecutada: %s %s @ %s", kind, fill.side, fill.qty, fill.price)
        return TickResult(kind, f"{fill.side} {fill.qty} @ {fill.price:.2f}")
