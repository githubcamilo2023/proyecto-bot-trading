"""Editar una estrategia = crear una versión NUEVA sin aprobar. Las versiones aprobadas nunca se modifican."""

import difflib
import re
from decimal import Decimal, InvalidOperation

from tradebot.strategy.describe import describe
from tradebot.strategy.rules import Strategy

MAX_INSTRUCTION = 400


def pct(text: str) -> Decimal:
    """'2', '2%' o '2,5' (porcentaje) -> fracción (0.02). Lanza ValueError si no es un número."""
    clean = text.strip().replace("%", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", clean):
        raise ValueError(f"no entiendo {text!r}: escribe un número, por ejemplo 2 o 2.5")
    try:
        return Decimal(clean) / 100
    except InvalidOperation:  # pragma: no cover - el regex ya lo descarta
        raise ValueError(f"número inválido: {text!r}") from None


def with_values(
    s: Strategy, size_pct: Decimal | None = None, stop_loss_pct: Decimal | None = None
) -> Strategy:
    """Misma estrategia con otro tamaño y/o stop-loss. Pasa otra vez por la validación completa."""
    data = s.model_dump(mode="json")
    if size_pct is not None:
        data["size_pct"] = str(size_pct)
    if stop_loss_pct is not None:
        data["stop_loss_pct"] = str(stop_loss_pct)
    return Strategy.model_validate(data)


def edit_directive(s: Strategy, instruction: str) -> str:
    """Directriz para el modelo: estrategia actual + cambio pedido. La salida se valida como siempre."""
    text = instruction.strip()
    if not text:
        raise ValueError("escribe qué quieres cambiar")
    if len(text) > MAX_INSTRUCTION:
        raise ValueError(f"el cambio es demasiado largo (máximo {MAX_INSTRUCTION} caracteres)")
    current = s.model_dump_json(exclude={"version"})
    return (
        "Modifica la estrategia existente aplicando SOLO el cambio pedido; conserva todo lo demás idéntico y "
        f"responde con el JSON completo. Estrategia actual: {current}. Cambio pedido: {text}"
    )


def changes(old: Strategy, new: Strategy) -> list[str]:
    """Diferencias legibles entre dos versiones (líneas '- antes' / '+ después')."""
    a, b = describe(old).splitlines()[1:], describe(new).splitlines()[1:]
    return [ln for ln in difflib.ndiff(a, b) if ln.startswith(("- ", "+ "))]
