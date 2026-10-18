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
from tradebot.strategy.engine import min_candles, signal, window

MIN_TRADES = 30
MIN_SEGMENT = 50


@dataclass(frozen=True, slots=True)
class Segment:
    start: datetime
    end: datetime
    result: Result


@dataclass(frozen=True, slots=True)
class Checks:
    """Verificaciones de confianza (ideas de freqtrade: recursive-analysis y robustez en vez de hyperopt)."""

    window_checked: int  # decisiones comparadas
    window_diff: int  # cuántas cambian si el indicador ve el DOBLE de historia
    stability: tuple[tuple[str, Decimal], ...]  # (variante, retorno total)

    def render(self) -> list[str]:
        lines = ["", "VERIFICACIONES", "  Datos futuros: imposibles por diseño (decide con la vela cerrada y "
                 "ejecuta en la apertura siguiente)."]  # fmt: skip
        if not self.window_checked:
            lines.append("  Ventana: no hay historia suficiente para comprobarla (usa más --days).")
        elif self.window_diff == 0:
            lines.append(f"  Ventana: OK. {self.window_checked} decisiones iguales con el doble de historia.")
        else:
            lines.append(
                f"  Ventana: ATENCIÓN. {self.window_diff} de {self.window_checked} decisiones cambian si el "
                "indicador ve más historia: el bot en vivo podría decidir distinto que el backtest."
            )
        rets = [r for _, r in self.stability]
        detail = " · ".join(f"{name} {r:+.2%}" for name, r in self.stability)
        stable = all(r > 0 for r in rets) or all(r <= 0 for r in rets)
        verdict = (
            "estable: el resultado no depende de un número exacto"
            if stable
            else "FRÁGIL: con periodos parecidos el resultado cambia de signo; probable sobreajuste"
        )
        lines.append(f"  Estabilidad (periodos −20 % / +20 %): {detail} → {verdict}.")
        return lines


@dataclass(frozen=True, slots=True)
class Evaluation:
    label: str
    segments: tuple[Segment, ...]
    checks: Checks | None = None

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
        if self.checks is not None:
            lines += self.checks.render()
        return "\n".join(lines)


def scaled(strategy: Strategy, factor: Decimal) -> Strategy:
    """La misma estrategia con TODOS los periodos multiplicados (mínimo 2). Pasa por la validación."""

    def walk(node: object) -> object:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            period = out.get("period")
            if isinstance(period, int):
                out["period"] = max(2, int((Decimal(period) * factor).to_integral_value()))
            return out
        return [walk(x) for x in node] if isinstance(node, list) else node

    return Strategy.model_validate(walk(strategy.model_dump(mode="json")))


def window_consistency(strategy: Strategy, candles: list[Candle], sample: int = 200) -> tuple[int, int]:
    """(decisiones comparadas, decisiones distintas) entre ver `window` velas y ver el doble."""
    w = window(strategy)
    first = 2 * w - 1
    idx = range(max(first, len(candles) - sample), len(candles))
    checked = diff = 0
    for i in idx:
        short, long_ = candles[i + 1 - w : i + 1], candles[i + 1 - 2 * w : i + 1]
        for in_pos in (False, True):
            checked += 1
            diff += signal(strategy, short, in_pos) is not signal(strategy, long_, in_pos)
    return checked, diff


def evaluate(
    strategy: Strategy,
    candles: list[Candle],
    segments: int = 4,
    limits: RiskLimits | None = None,
    equity: Decimal = DEFAULT_EQUITY,
    fee_pct: Decimal = FEE_PCT,
    slippage_pct: Decimal = SLIPPAGE_PCT,
    verify: bool = True,
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
    checks = None
    if verify:
        variants = [
            ("base", strategy),
            ("−20 %", scaled(strategy, Decimal("0.8"))),
            ("+20 %", scaled(strategy, Decimal("1.2"))),
        ]
        stability = tuple(
            (name, run(s, candles, limits, equity, fee_pct, slippage_pct).total_return)
            for name, s in variants
        )
        checks = Checks(*window_consistency(strategy, candles), stability)
    return Evaluation(label, tuple(out), checks)
