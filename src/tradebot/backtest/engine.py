from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from tradebot.config import DEFAULT_EQUITY, FEE_PCT, MIN_WINDOW, QTY_STEP, SLIPPAGE_PCT
from tradebot.models import Candle, Order, RiskLimits, Side
from tradebot.risk import RiskEngine
from tradebot.strategy import Signal, Strategy, signal
from tradebot.strategy.engine import min_candles

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
    window = max(
        MIN_WINDOW, 3 * warm
    )  # mismas velas que vería el bot en vivo: ventana fija, no todo el historial
    risk = RiskEngine(limits or RiskLimits(), equity)
    cash, fees = equity, ZERO
    pos: _Pos | None = None
    rejected: Counter[str] = Counter()
    trades: list[Trade] = []
    curve: list[tuple[datetime, Decimal]] = []
    pending = Signal.HOLD
    day, day_start, last_equity = candles[0].ts.date(), equity, equity

    for i, c in enumerate(candles):
        if c.ts.date() != day:  # día UTC nuevo: la pérdida diaria se mide desde el último cierre
            day, day_start = c.ts.date(), last_equity
        eq_open = cash + (pos.qty if pos else ZERO) * c.open
        risk.set_equity(eq_open)
        risk.observe(eq_open)  # igual que en vivo: caída máxima desde el pico -> kill switch (no más compras)
        day_pnl = eq_open - day_start
        sell_px, stopped = None, False
        if pending is Signal.BUY and pos is None:
            px = c.open * (1 + slippage_pct)
            notional = min(eq_open * strategy.size_pct, cash / (1 + fee_pct))
            q = (notional / px).quantize(QTY_STEP, ROUND_DOWN)
            d = risk.check(Order(strategy.symbol, Side.BUY, q, px, c.ts), 0, day_pnl)
            if d.allowed:
                fee = q * px * fee_pct
                cash -= q * px + fee
                pos = _Pos(c.ts, px, q, q * px + fee)
                fees += fee
            else:
                rejected[d.reason] += 1
        elif pending is Signal.SELL and pos is not None:
            sell_px = c.open * (1 - slippage_pct)
        if pos is not None and sell_px is None:
            # stop-loss: si la vela toca el nivel, sale ahí (o en la apertura si abrió ya por debajo: hueco)
            stop = pos.price * (1 - strategy.stop_loss_pct)
            if c.low <= stop:
                sell_px, stopped = min(stop, c.open) * (1 - slippage_pct), True
        if pos is not None and sell_px is not None:
            d = risk.check(Order(strategy.symbol, Side.SELL, pos.qty, sell_px, c.ts), 1, day_pnl)
            if d.allowed:
                proceeds = pos.qty * sell_px
                fee = proceeds * fee_pct
                cash, fees = cash + proceeds - fee, fees + fee
                trades.append(
                    Trade(
                        pos.ts,
                        c.ts,
                        pos.price,
                        sell_px,
                        pos.qty,
                        pos.cost,
                        proceeds - fee - pos.cost,
                        stopped,
                    )
                )
                pos = None
            else:
                rejected[d.reason] += 1
        last_equity = cash + (pos.qty if pos else ZERO) * c.close
        curve.append((c.ts, last_equity))
        pending = Signal.HOLD
        if i < n - 1:
            pending = signal(strategy, candles[max(0, i + 1 - window) : i + 1], in_position=pos is not None)

    start = min(warm, n - 1)  # el "comprar y mantener" arranca cuando la estrategia ya podía operar
    return Result(
        label=f"{strategy.name} v{strategy.version} · {strategy.symbol} {strategy.timeframe}",
        initial_equity=equity,
        final_equity=last_equity,
        trades=tuple(trades),
        curve=tuple(curve),
        fees=fees,
        rejected=dict(rejected),
        open_at_end=pos is not None,
        buy_hold_return=candles[-1].close / candles[start].open - 1,
    )
