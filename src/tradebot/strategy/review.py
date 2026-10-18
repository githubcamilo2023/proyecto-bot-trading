"""Pre-vuelo: qué estrategia se aplicaría al arrancar el bot, por qué esa y de dónde salió.

Todo sale de lo que ya existe en disco (archivos de estrategia, aprobación firmada y auditoría). Nada de esto
lo redacta un modelo de IA: es lo que el bot realmente va a usar.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tradebot.models import RiskLimits
from tradebot.strategy.describe import describe
from tradebot.strategy.rules import Strategy
from tradebot.strategy.store import StrategyStore

MAX_AUDIT_BYTES = 5_000_000
MAX_HOPS = 10
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean(text: object) -> str:
    """Texto de la auditoría -> una línea sin caracteres de control (evita secuencias ANSI en la consola)."""
    return _CONTROL.sub(" ", str(text))


@dataclass(frozen=True, slots=True)
class Review:
    strategy: Strategy
    approved_by: str
    approved_at: str
    newer: tuple[int, ...]  # versiones posteriores (sin aprobación válida: el bot las ignora)
    origin: str | None


def _records(audit_path: str | Path) -> dict[str, dict[str, Any]]:
    """Último registro de origen (propuesta o edición) por «nombre vN». Solo lectura."""
    try:
        with Path(audit_path).open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - MAX_AUDIT_BYTES))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for ln in raw.splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if (
            isinstance(r, dict)
            and r.get("action") in {"llm.propose", "strategy.edit", "strategy.import"}
            and r.get("result") == "ok"
            and isinstance(r.get("details"), dict)
        ):
            out[str(r.get("subject"))] = {"action": r["action"], **r["details"]}
    return out


def origin_of(audit_path: str | Path, name: str, version: int) -> str | None:
    """De dónde salió esa versión: tu frase original y las ediciones, si constan en la auditoría."""
    recs = _records(audit_path)

    def walk(v: int, hops: int) -> str | None:
        rec = recs.get(f"{name} v{v}")
        if rec is None or hops > MAX_HOPS:
            return None
        base_v = rec.get("from_version")
        if rec["action"] == "strategy.edit" or isinstance(base_v, int):
            before = walk(base_v, hops + 1) if isinstance(base_v, int) else None
            step = f"luego ajustada: {_clean(rec.get('summary', 'cambios manuales'))}"
            if rec["action"] == "llm.propose":
                step = f"luego editada con tu frase: «{_clean(rec.get('summary', ''))}»"
            return f"{before or 'origen anterior no consta'} → {step}"
        if rec["action"] == "strategy.import":
            return f"importada de la {_clean(rec.get('source', 'plantilla'))} (biblioteca documentada)"
        preview = rec.get("preview")
        return (
            f"la creó una IA a partir de tu frase «{_clean(preview)}»" if isinstance(preview, str) else None
        )

    return walk(version, 0)


def review(store: StrategyStore, name: str, audit_path: str | Path) -> Review:
    """Lanza ApprovalError si no hay ninguna versión aprobada con firma válida."""
    s = store.load_approved(name)
    rec = store.check_approval(name, s.version)
    newer = tuple(v for v in store.versions(name) if v > s.version)
    return Review(s, str(rec["by"]), str(rec["at"]), newer, origin_of(audit_path, name, s.version))


def render_review(r: Review, limits: RiskLimits, killed: bool = False) -> str:
    s = r.strategy
    when = r.approved_at[:16].replace("T", " ")
    lines = [
        f"ESTRATEGIA QUE SE APLICARÍA: «{s.name}» versión {s.version}",
        f"  Por qué esta: es la versión más reciente con aprobación válida (aprobada por {r.approved_by}"
        f" el {when} UTC).",
    ]
    if r.newer:
        olds = ", ".join(f"v{v}" for v in r.newer)
        lines.append(
            f"  Ojo: hay versiones más nuevas SIN aprobar ({olds}). El bot las ignora hasta que las apruebes."
        )
    lines.append(
        f"  Origen: {r.origin}."
        if r.origin
        else "  Origen: no consta en la auditoría actual (se creó antes de archivarla, o a mano)."
    )
    lines += ["", describe(s), ""]
    lines.append(
        "  Límites de riesgo que mandan SIEMPRE (la estrategia no puede saltárselos): "
        f"máx. {limits.max_order_pct * 100:.0f}% del capital por orden · "
        f"pérdida diaria máx. {limits.max_daily_loss_pct * 100:.0f}% · "
        f"caída máx. {limits.max_drawdown_pct * 100:.0f}% · máx. {limits.max_open_positions} posiciones.\n"
        f"  Protecciones: espera {limits.cooldown_candles} vela(s) tras vender · pausa de "
        f"{limits.stoploss_guard_pause} velas si saltan {limits.stoploss_guard_count} stop-loss en "
        f"{limits.stoploss_guard_window} velas."
    )
    if killed:
        lines.append("  ¡Kill switch ACTIVADO! El bot NO comprará hasta que lo quites.")
    return "\n".join(lines)
