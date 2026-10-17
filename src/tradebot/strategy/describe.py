"""Descripción en español de una estrategia, generada por CÓDIGO a partir del esquema validado.

Es lo que el usuario lee antes de aprobar. Nunca la escribe el LLM: una descripción redactada por el
mismo modelo que propuso la regla podría no corresponder con lo que la regla realmente hace.
"""

from decimal import Decimal

from tradebot.strategy.rules import AllOf, AnyOf, Close, Cmp, Cond, Moving, Operand, Strategy

_OPS = {
    "<": "es menor que",
    "<=": "es menor o igual que",
    ">": "es mayor que",
    ">=": "es mayor o igual que",
    "crosses_above": "cruza hacia arriba a",
    "crosses_below": "cruza hacia abajo a",
}
_KINDS = {"sma": "la media simple de {p} velas", "ema": "la media exponencial de {p} velas"}


def operand_text(o: Operand) -> str:
    match o:
        case Close():
            return "el precio de cierre"
        case Moving(kind="rsi"):
            return f"el RSI de {o.period} periodos"
        case Moving():
            return _KINDS[o.kind].format(p=o.period)
        case Decimal():
            return f"{o:f}"
    raise AssertionError("operando no contemplado")  # pragma: no cover


def cmp_text(c: Cmp) -> str:
    return f"{operand_text(c.left)} {_OPS[c.op]} {operand_text(c.right)}"


def _cond(c: Cond, depth: int = 0) -> list[str]:
    pad = "  " * depth
    match c:
        case Cmp():
            return [f"{pad}- {cmp_text(c)}"]
        case AllOf() | AnyOf():
            head = "TODAS estas condiciones" if isinstance(c, AllOf) else "ALGUNA de estas condiciones"
            return [f"{pad}- {head}:", *(ln for x in c.of for ln in _cond(x, depth + 1))]
    raise AssertionError("condición no contemplada")  # pragma: no cover


def describe(s: Strategy) -> str:
    lines = [
        f"Estrategia «{s.name}» (versión {s.version}) sobre {s.symbol}, velas de {s.timeframe}.",
        "COMPRA cuando se cumple:",
        *_cond(s.entry),
        "VENDE cuando se cumple:",
        *_cond(s.exit),
        f"Tamaño pedido: {s.size_pct * 100:f}% del capital por operación "
        "(el motor de riesgo puede reducirlo o rechazarlo).",
        f"Stop-loss: vende si el precio cae {s.stop_loss_pct * 100:f}% desde la entrada.",
    ]
    if s.notes:
        lines.append(f"Notas: {s.notes}")
    return "\n".join(lines)
