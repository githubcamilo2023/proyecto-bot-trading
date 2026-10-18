"""Evaluación por tramos: ¿el resultado se sostiene en periodos distintos o fue un solo golpe de suerte?

Corta el historial en N tramos consecutivos y simula cada uno por separado con las mismas reglas.
No optimiza nada: solo mide consistencia. No sustituye a probar en paper con datos nuevos.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradebot.backtest.engine import Result, run
from tradebot.config import DEFAULT_EQUITY, FEE_PCT, SLIPPAGE_PCT
from tradebot.models import Candle, RiskLimits
from tradebot.strategy import Strategy
from tradebot.strategy.engine import min_candles

MIN_TRADES = 30
MIN_SEGMENT = 50


@dataclass(frozen=True, slots=True)
class Segment:
    start: datetime
    end: datetime
    result: Result


@dataclass(frozen=True, slots=True)
class Evaluation:
    label: str
    segments: tuple[Segment, ...]

    @property
    def trades(self) -> int:
        return sum(len(g.result.trades) for g in self.segments)

    @property
    def positive(self) -> int:
        return sum(g.result.total_return > 0 for g in self.segments)

    @property
    def beat_hold(self) -> int:
        return sum(g.result.total_return > g.result.buy_hold_return for g in self.segments)

    @property
    def worst_drawdown(self) -> Decimal:
        return max(g.result.max_drawdown for g in self.segments)

    def render(self) -> str:
        n = len(self.segments)
        lines = [f"Evaluación por tramos: {self.label}", ""]
        for i, g in enumerate(self.segments, 1):
            r = g.result
            lines.append(
                f"  Tramo {i}: {g.start:%Y-%m-%d} → {g.end:%Y-%m-%d} · retorno {r.total_return:+.2%}"
                f" · mantener {r.buy_hold_return:+.2%} · caída máx. {r.max_drawdown:.2%}"
                f" · {len(r.trades)} operaciones"
            )
        lines += [
            "",
            f"Tramos con ganancia: {self.positive} de {n} · tramos que superan a comprar y mantener: "
            f"{self.beat_hold} de {n}",
            f"Operaciones en total: {self.trades} · peor caída máxima: {self.worst_drawdown:.2%}",
        ]
        if self.trades < MIN_TRADES:
            lines.append(
                f"Muestra pequeña ({self.trades} < {MIN_TRADES} operaciones): no se puede concluir nada."
            )
        elif self.positive < n:
            lines.append("No es consistente: hay tramos con pérdidas. Revisa si depende de un solo periodo.")
        else:
            lines.append("Fue consistente en estos tramos. Eso no garantiza el futuro: sigue en paper.")
        lines.append(
            "Si cambias parámetros mirando este informe, vuelve a probar con datos que no hayas visto."
        )
        return "\n".join(lines)


def evaluate(
    strategy: Strategy,
    candles: list[Candle],
    segments: int = 4,
    limits: RiskLimits | None = None,
    equity: Decimal = DEFAULT_EQUITY,
    fee_pct: Decimal = FEE_PCT,
    slippage_pct: Decimal = SLIPPAGE_PCT,
) -> Evaluation:
    if segments < 2:
        raise ValueError("--segments debe ser al menos 2")
    size = len(candles) // segments
    if size < MIN_SEGMENT:
        raise ValueError(f"muy poca historia para {segments} tramos ({len(candles)} velas): usa más --days")
    warm = min_candles(strategy)
    out = []
    for k in range(segments):
        lo, hi = k * size, (k + 1) * size if k < segments - 1 else len(candles)
        part = candles[max(0, lo - warm) : hi]  # calentamiento: los indicadores ya están listos al empezar
        out.append(
            Segment(
                candles[lo].ts, candles[hi - 1].ts, run(strategy, part, limits, equity, fee_pct, slippage_pct)
            )
        )
    label = f"{strategy.name} v{strategy.version} · {strategy.symbol} {strategy.timeframe}"
    return Evaluation(label, tuple(out))
