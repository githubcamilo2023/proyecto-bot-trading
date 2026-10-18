from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from tradebot.config import DEFAULT_EQUITY, FEE_PCT, QTY_STEP, SLIPPAGE_PCT
from tradebot.models import Candle, Order, RiskLimits, Side
from tradebot.risk import RiskEngine
from tradebot.risk.protections import Exit, buy_block
from tradebot.strategy import Signal, Strategy, signal
from tradebot.strategy.engine import min_candles, window
from tradebot.timeframe import tf_seconds

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Trade:
    entry_ts: datetime
    exit_ts: datetime
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    cost: Decimal  # lo gastado al entrar, comisión incluida
    pnl: Decimal  # neto de comisiones y deslizamiento
    stopped: bool = False  # salió por stop-loss y no por la regla de salida


@dataclass(slots=True)
class _Pos:
    """Posición abierta (solo existe dentro de `run`)."""

    ts: datetime
    price: Decimal
    qty: Decimal
    cost: Decimal  # lo gastado al entrar, comisión incluida


@dataclass(frozen=True, slots=True)
class Result:
    label: str
    initial_equity: Decimal
    final_equity: Decimal
    trades: tuple[Trade, ...]
    curve: tuple[tuple[datetime, Decimal], ...]
    fees: Decimal
    rejected: dict[str, int]
    open_at_end: bool
    buy_hold_return: Decimal

    @property
    def total_return(self) -> Decimal:
        return self.final_equity / self.initial_equity - 1

    @property
    def max_drawdown(self) -> Decimal:
        peak, worst = self.curve[0][1], ZERO
        for _, e in self.curve:
            peak = max(peak, e)
            worst = max(worst, (peak - e) / peak)
        return worst

    @property
    def win_rate(self) -> Decimal | None:
        return Decimal(sum(t.pnl > 0 for t in self.trades)) / len(self.trades) if self.trades else None

    @property
    def profit_factor(self) -> Decimal | None:
        """Ganancias brutas / pérdidas brutas. None si no hay pérdidas que dividir."""
        losses = -sum((t.pnl for t in self.trades if t.pnl < 0), ZERO)
        wins = sum((t.pnl for t in self.trades if t.pnl > 0), ZERO)
        return wins / losses if losses > 0 else None

    def summary(self) -> str:
        wr = "—" if self.win_rate is None else f"{self.win_rate:.1%}"
        lines = [
            f"Estrategia: {self.label}",
            f"Periodo: {self.curve[0][0]:%Y-%m-%d} → {self.curve[-1][0]:%Y-%m-%d} ({len(self.curve)} velas)",
            f"Capital: {self.initial_equity:,.2f} → {self.final_equity:,.2f} ({self.total_return:+.2%})",
            f"Comprar y mantener: {self.buy_hold_return:+.2%}",
            f"Caída máxima: {self.max_drawdown:.2%}",
            f"Operaciones cerradas: {len(self.trades)} · aciertos {wr} · comisiones {self.fees:,.2f}",
            f"Salidas por stop-loss: {sum(t.stopped for t in self.trades)}"
            + ("" if self.profit_factor is None else f" · factor de beneficio {self.profit_factor:.2f}"),
        ]
        if self.open_at_end:
            lines.append("Hay una posición abierta al final (valorada al último cierre).")
        lines += [f"Órdenes rechazadas por riesgo: {n} × {why}" for why, n in self.rejected.items()]
        if "kill switch activo" in self.rejected:
            lines.append(
                "El kill switch saltó por la caída máxima de capital: desde ahí no volvió a comprar."
            )
        if len(self.trades) < 30:
            lines.append("Aviso: menos de 30 operaciones; la muestra es pequeña y no prueba nada.")
        return "\n".join(lines)


@dataclass(slots=True)
class _Sim:
    """Estado de la simulación. Cada método es un paso de la vela; `run` solo los ordena."""

    strategy: Strategy
    risk: RiskEngine
    fee_pct: Decimal
    slippage_pct: Decimal
    cash: Decimal
    fees: Decimal = ZERO
    pos: _Pos | None = None
    rejected: Counter[str] = field(default_factory=Counter)
    trades: list[Trade] = field(default_factory=list)
    exits: list[Exit] = field(default_factory=list)

    def equity(self, price: Decimal) -> Decimal:
        return self.cash + (self.pos.qty if self.pos else ZERO) * price

    def buy(self, c: Candle, eq_open: Decimal, day_pnl: Decimal) -> None:
        s = self.strategy
        if blocked := buy_block(self.exits, c.ts, tf_seconds(s.timeframe), self.risk.limits):
            self.rejected[blocked] += 1
            return
        px = c.open * (1 + self.slippage_pct)
        notional = min(eq_open * s.order_fraction, self.cash / (1 + self.fee_pct))
        q = (notional / px).quantize(QTY_STEP, ROUND_DOWN)
        d = self.risk.check(Order(s.symbol, Side.BUY, q, px, c.ts), 0, day_pnl)
        if not d.allowed:
            self.rejected[d.reason] += 1
            return
        fee = q * px * self.fee_pct
        self.cash -= q * px + fee
        self.pos = _Pos(c.ts, px, q, q * px + fee)
        self.fees += fee

    def exit_price(self, c: Candle, sell_signal: bool) -> tuple[Decimal | None, bool]:
        """Precio de salida en esta vela (regla o stop-loss) y si fue por stop; None si no sale."""
        if self.pos is None:
            return None, False
        if sell_signal:
            return c.open * (1 - self.slippage_pct), False
        # stop-loss: si la vela toca el nivel, sale ahí (o en la apertura si abrió ya por debajo: hueco)
        stop = self.pos.price * (1 - self.strategy.stop_loss_pct)
        if c.low <= stop:
            return min(stop, c.open) * (1 - self.slippage_pct), True
        return None, False

    def sell(self, c: Candle, px: Decimal, stopped: bool, day_pnl: Decimal) -> None:
        pos = self.pos
        assert pos is not None  # noqa: S101 - invariante: solo se llama con posición
        d = self.risk.check(Order(self.strategy.symbol, Side.SELL, pos.qty, px, c.ts), 1, day_pnl)
        if not d.allowed:
            self.rejected[d.reason] += 1
            return
        proceeds = pos.qty * px
        fee = proceeds * self.fee_pct
        self.cash, self.fees = self.cash + proceeds - fee, self.fees + fee
        self.trades.append(
            Trade(pos.ts, c.ts, pos.price, px, pos.qty, pos.cost, proceeds - fee - pos.cost, stopped)
        )
        self.exits.append(Exit(c.ts, stopped))
        self.pos = None


def run(
    strategy: Strategy,
    candles: list[Candle],
    limits: RiskLimits | None = None,
    equity: Decimal = DEFAULT_EQUITY,
    fee_pct: Decimal = FEE_PCT,
    slippage_pct: Decimal = SLIPPAGE_PCT,
) -> Result:
    """Simula vela a vela. La señal sale del CIERRE de la vela i y se ejecuta en la APERTURA de la i+1
    (sin mirar el futuro). Spot, una posición a la vez; toda orden pasa por el motor de riesgo."""
    if len(candles) < 2:
        raise ValueError("se necesitan al menos 2 velas")
    n, warm = len(candles), min_candles(strategy)
    win = window(strategy)  # mismas velas que vería el bot en vivo: ventana fija, no todo el historial
    sim = _Sim(strategy, RiskEngine(limits or RiskLimits(), equity), fee_pct, slippage_pct, equity)
    curve: list[tuple[datetime, Decimal]] = []
    pending = Signal.HOLD
    day, day_start, last_equity = candles[0].ts.date(), equity, equity

    for i, c in enumerate(candles):
        if c.ts.date() != day:  # día UTC nuevo: la pérdida diaria se mide desde el último cierre
            day, day_start = c.ts.date(), last_equity
        eq_open = sim.equity(c.open)
        sim.risk.set_equity(eq_open)
        sim.risk.observe(eq_open)  # igual que en vivo: caída máxima desde el pico -> kill switch
        day_pnl = eq_open - day_start
        if pending is Signal.BUY and sim.pos is None:
            sim.buy(c, eq_open, day_pnl)
        px, stopped = sim.exit_price(c, pending is Signal.SELL)
        if px is not None:
            sim.sell(c, px, stopped, day_pnl)
        last_equity = sim.equity(c.close)
        curve.append((c.ts, last_equity))
        pending = Signal.HOLD
        if i < n - 1:
            pending = signal(strategy, candles[max(0, i + 1 - win) : i + 1], in_position=sim.pos is not None)

    start = min(warm, n - 1)  # el "comprar y mantener" arranca cuando la estrategia ya podía operar
    return Result(
        label=f"{strategy.name} v{strategy.version} · {strategy.symbol} {strategy.timeframe}",
        initial_equity=equity,
        final_equity=last_equity,
        trades=tuple(sim.trades),
        curve=tuple(curve),
        fees=sim.fees,
        rejected=dict(sim.rejected),
        open_at_end=sim.pos is not None,
        buy_hold_return=candles[-1].close / candles[start].open - 1,
    )
