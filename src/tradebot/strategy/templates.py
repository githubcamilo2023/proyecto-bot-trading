"""Biblioteca de plantillas: estrategias de familias documentadas, con su fuente y qué esperar de ellas.

Son puntos de partida para EVALUAR, no recomendaciones: ninguna está aprobada y ninguna garantiza nada.
Viven dentro del paquete (`strategy/templates/*.json`) y se validan con el mismo esquema estricto.
"""

from pathlib import Path

from tradebot.strategy.rules import Strategy

TEMPLATES_DIR = Path(__file__).parent / "templates"


def names() -> list[str]:
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.json"))


def load(name: str) -> Strategy:
    if name not in names():
        raise FileNotFoundError(f"no existe la plantilla {name!r}; disponibles: {', '.join(names())}")
    return Strategy.model_validate_json((TEMPLATES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def catalog() -> list[Strategy]:
    return [load(n) for n in names()]


def for_prompt() -> str:
    """Catálogo compacto para dar contexto al modelo (sin notas largas: solo familia y reglas)."""
    lines = []
    for s in catalog():
        family = s.notes.split(".")[0].removeprefix("Familia: ")
        rules = s.model_dump_json(include={"timeframe", "entry", "exit", "stop_loss_pct"})
        lines.append(f"- {s.name} ({family}): {rules}")
    return "\n".join(lines)
