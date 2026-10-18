from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True, slots=True)
class Candle:
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class Order:
    symbol: str
    side: Side
    qty: Decimal
    price: Decimal
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def notional(self) -> Decimal:
        return self.qty * self.price


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    qty: Decimal
    entry_price: Decimal
    entry_candle_ts: datetime  # vela cuyo cierre originó la entrada; el stop solo mira velas posteriores
    cost: Decimal  # lo gastado al entrar, comisión incluida


@dataclass(frozen=True, slots=True)
class Fill:
    client_id: str
    symbol: str
    side: Side
    qty: Decimal
    price: Decimal
    fee: Decimal
    ts: datetime
    pnl: Decimal | None  # solo en ventas, neto de comisiones


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Fracciones del capital (0.02 = 2%). Todos los valores son parametrizables."""

    max_order_pct: Decimal = Decimal("0.02")
    max_daily_loss_pct: Decimal = Decimal("0.03")
    max_open_positions: int = 3
    max_drawdown_pct: Decimal = Decimal("0.10")  # caída desde el máximo de capital que activa el kill switch

    def __post_init__(self) -> None:
        pcts = (self.max_order_pct, self.max_daily_loss_pct, self.max_drawdown_pct)
        pcts_ok = all(0 < p <= 1 for p in pcts)
        if not (pcts_ok and self.max_open_positions > 0):
            raise ValueError("límites de riesgo inválidos")
