"""Indicadores puros sobre cierres (ascendentes, solo velas cerradas). Devuelven None si faltan datos."""

from collections.abc import Sequence
from decimal import Decimal

D0 = Decimal(0)


def sma(v: Sequence[Decimal], n: int) -> Decimal | None:
    return sum(v[-n:], D0) / n if len(v) >= n else None


def ema(v: Sequence[Decimal], n: int) -> Decimal | None:
    """Semilla = SMA de los primeros n cierres; luego suavizado exponencial."""
    if len(v) < n:
        return None
    k, e = Decimal(2) / (n + 1), sum(v[:n], D0) / n
    for x in v[n:]:
        e = x * k + e * (1 - k)
    return e


def rsi(v: Sequence[Decimal], n: int) -> Decimal | None:
    """RSI de Wilder. Sin movimiento (todo plano) devuelve 50."""
    if len(v) <= n:
        return None
    d = [b - a for a, b in zip(v, v[1:], strict=False)]
    g, lo = sum((x for x in d[:n] if x > 0), D0) / n, sum((-x for x in d[:n] if x < 0), D0) / n
    for x in d[n:]:
        g, lo = (g * (n - 1) + max(x, D0)) / n, (lo * (n - 1) + max(-x, D0)) / n
    if g == lo == 0:
        return Decimal(50)
    return Decimal(100) if lo == 0 else 100 - 100 / (1 + g / lo)
