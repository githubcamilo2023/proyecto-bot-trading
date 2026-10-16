"""Broker simulado (paper trading) con estado persistente e idempotencia por id de orden.

Qué garantiza:
- Enviar dos veces la misma orden (mismo `client_id`) NO la ejecuta dos veces: devuelve el fill original.
- El estado se escribe de forma atómica ANTES de dar la orden por ejecutada; si no se puede guardar, la
  orden falla y el estado en memoria no cambia.
- Un archivo de estado ilegible o incoherente impide arrancar (nunca se "arregla" en silencio).
"""

import json
import logging
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradebot.config import DEFAULT_EQUITY, FEE_PCT, SLIPPAGE_PCT
from tradebot.models import Fill, Order, Position, Side

log = logging.getLogger("tradebot.paper")
ZERO = Decimal(0)


class BrokerError(Exception):
    """La orden no se pudo ejecutar o el estado del broker no es fiable."""


def _fill_to_json(f: Fill) -> dict[str, Any]:
    return {
        "client_id": f.client_id,
        "symbol": f.symbol,
        "side": f.side.value,
        "qty": str(f.qty),
        "price": str(f.price),
        "fee": str(f.fee),
        "ts": f.ts.isoformat(),
        "pnl": None if f.pnl is None else str(f.pnl),
    }


def _fill_from_json(d: dict[str, Any]) -> Fill:
    return Fill(
        d["client_id"],
        d["symbol"],
        Side(d["side"]),
        Decimal(d["qty"]),
        Decimal(d["price"]),
        Decimal(d["fee"]),
        datetime.fromisoformat(d["ts"]),
        None if d["pnl"] is None else Decimal(d["pnl"]),
    )


class PaperBroker:
    def __init__(
        self,
        state_path: str | Path,
        equity: Decimal = DEFAULT_EQUITY,
        fee_pct: Decimal = FEE_PCT,
        slippage_pct: Decimal = SLIPPAGE_PCT,
    ) -> None:
        self.path = Path(state_path)
        self.fee_pct, self.slippage_pct = fee_pct, slippage_pct
        self.cash: Decimal = equity
        self.position: Position | None = None
        self.fills: dict[str, Fill] = {}
        self.day: date | None = None
        self.day_start: Decimal = equity
        if self.path.exists():
            self._load()
        else:
            self._save(self._snapshot(self.cash, None, self.fills, self.day, self.day_start))

    # --- persistencia -----------------------------------------------------------------------

    @staticmethod
    def _snapshot(
        cash: Decimal, pos: Position | None, fills: dict[str, Fill], day: date | None, day_start: Decimal
    ) -> dict[str, Any]:
        return {
            "cash": str(cash),
            "position": None
            if pos is None
            else {
                "symbol": pos.symbol,
                "qty": str(pos.qty),
                "entry_price": str(pos.entry_price),
                "entry_candle_ts": pos.entry_candle_ts.isoformat(),
                "cost": str(pos.cost),
            },
            "fills": {k: _fill_to_json(v) for k, v in fills.items()},
            "day": None if day is None else day.isoformat(),
            "day_start": str(day_start),
        }

    def _save(self, snap: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap), encoding="utf-8")
            os.replace(tmp, self.path)  # atómico
        except OSError as e:
            raise BrokerError(f"no se pudo guardar el estado del broker: {e}") from e

    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            p = d["position"]
            self.cash = Decimal(d["cash"])
            self.position = (
                None
                if p is None
                else Position(
                    p["symbol"],
                    Decimal(p["qty"]),
                    Decimal(p["entry_price"]),
                    datetime.fromisoformat(p["entry_candle_ts"]),
                    Decimal(p["cost"]),
                )
            )
            self.fills = {k: _fill_from_json(v) for k, v in d["fills"].items()}
            self.day = None if d["day"] is None else date.fromisoformat(d["day"])
            self.day_start = Decimal(d["day_start"])
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as e:
            raise BrokerError(f"estado del broker ilegible o incoherente en {self.path}: {e}") from e
        if self.cash < 0 or (self.position is not None and self.position.qty <= 0):
            raise BrokerError(f"estado del broker incoherente en {self.path} (efectivo o cantidad negativos)")

    def validate_for(self, symbol: str) -> None:
        """Reconciliación al arrancar: la posición guardada debe ser del símbolo que vamos a operar."""
        if self.position is not None and self.position.symbol != symbol:
            raise BrokerError(
                f"hay una posición abierta en {self.position.symbol}, distinta de {symbol}: revisa el estado"
            )

    # --- consultas --------------------------------------------------------------------------

    def equity(self, mark_price: Decimal) -> Decimal:
        return self.cash + (self.position.qty * mark_price if self.position else ZERO)

    def has_fill(self, client_id: str) -> bool:
        return client_id in self.fills

    def day_start_equity(self, now: datetime, mark_price: Decimal) -> Decimal:
        """Capital al inicio del día UTC actual (se fija en el primer ciclo del día)."""
        today = now.astimezone(UTC).date()
        if self.day != today:
            start = self.equity(mark_price)
            self._save(self._snapshot(self.cash, self.position, self.fills, today, start))
            self.day, self.day_start = today, start
        return self.day_start

    # --- órdenes ----------------------------------------------------------------------------

    def submit(self, order: Order, client_id: str, entry_candle_ts: datetime | None = None) -> Fill:
        """Ejecuta (simulado) a precio de referencia ± deslizamiento. Idempotente por `client_id`."""
        if prev := self.fills.get(client_id):
            log.info("orden %s ya ejecutada: se devuelve el fill original", client_id)
            return prev
        if order.qty <= 0 or order.price <= 0:
            raise BrokerError("cantidad o precio inválidos")
        pos: Position | None
        if order.side is Side.BUY:
            fill, cash, pos = self._buy(order, client_id, entry_candle_ts)
        else:
            fill, cash, pos = self._sell(order, client_id)
        fills = {**self.fills, client_id: fill}
        self._save(self._snapshot(cash, pos, fills, self.day, self.day_start))
        self.cash, self.position, self.fills = cash, pos, fills  # solo tras guardar con éxito
        return fill

    def _buy(self, o: Order, cid: str, entry_ts: datetime | None) -> tuple[Fill, Decimal, Position]:
        if self.position is not None:
            raise BrokerError("ya hay una posición abierta")
        if entry_ts is None:
            raise BrokerError("falta la vela de entrada")
        px = o.price * (1 + self.slippage_pct)
        fee = o.qty * px * self.fee_pct
        cost = o.qty * px + fee
        if cost > self.cash:
            raise BrokerError(f"fondos insuficientes: necesita {cost:.2f}, hay {self.cash:.2f}")
        fill = Fill(cid, o.symbol, Side.BUY, o.qty, px, fee, o.ts, None)
        return fill, self.cash - cost, Position(o.symbol, o.qty, px, entry_ts, cost)

    def _sell(self, o: Order, cid: str) -> tuple[Fill, Decimal, None]:
        pos = self.position
        if pos is None or pos.symbol != o.symbol:
            raise BrokerError("no hay posición que vender")
        if o.qty != pos.qty:
            raise BrokerError("solo se permite vender la posición completa")
        px = o.price * (1 - self.slippage_pct)
        proceeds = pos.qty * px
        fee = proceeds * self.fee_pct
        pnl = proceeds - fee - pos.cost
        return Fill(cid, o.symbol, Side.SELL, pos.qty, px, fee, o.ts, pnl), self.cash + proceeds - fee, None
