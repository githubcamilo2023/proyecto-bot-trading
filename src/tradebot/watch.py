"""Panel de consola de SOLO LECTURA: precio, posición, señales y eventos del bot, refrescado en vivo.

No puede enviar órdenes ni cambiar estado: lee los archivos que escribe el bot (estado del broker, latido,
kill switch, auditoría) y pide velas/precio públicos. Si algo falla al refrescar, lo muestra y sigue.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tradebot.execution.paper import PaperBroker
from tradebot.models import Fill, Position, RiskLimits
from tradebot.ports import MarketData
from tradebot.risk import RiskEngine
from tradebot.strategy.engine import Check, explain, min_candles
from tradebot.strategy.rules import Strategy
from tradebot.timeframe import tf_seconds

WIDTH = 78
SPARK = "▁▂▃▄▅▆▇█"
CLEAR = "\x1b[H\x1b[J"
SPARK_CANDLES = 48
LATE_GRACE = timedelta(seconds=120)


@dataclass(frozen=True, slots=True)
class Heartbeat:
    ts: datetime
    status: str
    detail: str
    next_due: datetime


@dataclass(frozen=True, slots=True)
class Snapshot:
    now: datetime
    strategy: Strategy
    note: str
    closes: list[Decimal]  # velas cerradas (+ precio en vivo al final, si lo hay)
    price: Decimal | None
    live: bool  # True si el precio viene del ticker; False si es el último cierre
    last_close: Decimal | None
    cash: Decimal | None  # None = el bot aún no creó su estado
    equity: Decimal | None
    day_start: Decimal | None
    position: Position | None
    fills: list[Fill]
    killed: bool
    heartbeat: Heartbeat | None | str  # None = sin latido; str = problema ("ilegible")
    entry: list[Check]
    exit: list[Check]
    events: list[tuple[str, str, str, str]]  # (hora, acción, sujeto, resultado)


# --- lectura -----------------------------------------------------------------------------------


def read_heartbeat(path: str | Path) -> Heartbeat | None | str:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return Heartbeat(
            datetime.fromisoformat(d["ts"]), d["status"], d["detail"], datetime.fromisoformat(d["next_due"])
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return "ilegible"


def read_events(path: str | Path, n: int = 6, max_bytes: int = 65536) -> list[tuple[str, str, str, str]]:
    """Últimos eventos de la auditoría (solo lectura, sin verificar la cadena: para eso está audit-verify)."""
    try:
        with Path(path).open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - max_bytes))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[tuple[str, str, str, str]] = []
    for ln in lines[-n - 1 :]:
        try:
            r = json.loads(ln)
            out.append((str(r["ts"])[11:19], str(r["action"]), str(r["subject"]), str(r["result"])))
        except (ValueError, KeyError, TypeError):
            continue  # primera línea cortada por el seek, o registro dañado
    return out[-n:]


def collect(
    strategy: Strategy,
    note: str,
    market: MarketData,
    price_fn: Callable[[str], Decimal] | None,
    *,
    state: str | Path,
    kill_state: str | Path,
    heartbeat: str | Path,
    audit: str | Path,
    now: datetime | None = None,
) -> Snapshot:
    now = now or datetime.now(UTC)
    candles = market.candles(strategy.symbol, strategy.timeframe, max(600, 3 * min_candles(strategy)))
    last_close = candles[-1].close if candles else None
    price, live = last_close, False
    if price_fn is not None:
        try:
            price, live = price_fn(strategy.symbol), True
        except Exception:  # noqa: BLE001 - el precio en vivo es opcional: se cae al último cierre
            price, live = last_close, False
    closes = [c.close for c in candles]
    broker = PaperBroker(state) if Path(state).exists() else None
    mark = price if price is not None else Decimal(0)
    fills = sorted(broker.fills.values(), key=lambda f: f.ts)[-5:] if broker else []
    today = now.astimezone(UTC).date()
    return Snapshot(
        now=now,
        strategy=strategy,
        note=note,
        closes=[*closes, price] if live and price is not None else closes,
        price=price,
        live=live,
        last_close=last_close,
        cash=broker.cash if broker else None,
        equity=broker.equity(mark) if broker else None,
        day_start=broker.day_start if broker and broker.day == today else None,
        position=broker.position if broker else None,
        fills=fills,
        killed=_killed(kill_state),
        heartbeat=read_heartbeat(heartbeat),
        entry=explain(strategy.entry, candles),
        exit=explain(strategy.exit, candles),
        events=read_events(audit),
    )


def _killed(kill_state: str | Path) -> bool:
    return RiskEngine(RiskLimits(), Decimal(0), kill_state).killed


# --- dibujo ------------------------------------------------------------------------------------

_CODES = {"green": "32", "red": "31", "yellow": "33", "dim": "2", "bold": "1", "cyan": "36"}


def sparkline(values: list[Decimal], width: int = SPARK_CANDLES) -> str:
    v = values[-width:]
    if not v:
        return ""
    lo, hi = min(v), max(v)
    if hi == lo:
        return SPARK[3] * len(v)
    return "".join(SPARK[int((x - lo) / (hi - lo) * (len(SPARK) - 1))] for x in v)


def _dur(td: timedelta) -> str:
    s = int(abs(td.total_seconds()))
    h, m = divmod(s // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _money(x: Decimal | None) -> str:
    return "—" if x is None else f"{x:,.2f}"


def _signed(x: Decimal) -> str:
    return f"{x:+,.2f}"


def _pct(x: Decimal) -> str:
    return f"{x * 100:+.2f}%"


def render(s: Snapshot, color: bool = True) -> str:
    def paint(text: str, *styles: str) -> str:
        if not color:
            return text
        return "".join(f"\x1b[{_CODES[st]}m" for st in styles) + text + "\x1b[0m"

    def pnl_color(x: Decimal) -> str:
        return "green" if x > 0 else "red" if x < 0 else "dim"

    st = s.strategy
    rule = "─" * WIDTH
    out = [
        paint(f" TRADEBOT · PAPER (simulado) · {st.name} v{st.version} · {st.symbol} {st.timeframe}", "bold")
        + paint(f"   {s.now:%Y-%m-%d %H:%M:%S} UTC", "dim"),
        rule,
    ]
    # estado del bot
    hb = s.heartbeat
    if isinstance(hb, Heartbeat):
        late = s.now > hb.next_due + LATE_GRACE
        if late:
            bot = paint("● SIN LATIDO", "red", "bold") + f" (debía latir hace {_dur(s.now - hb.next_due)})"
        else:
            nxt = f"próximo ciclo en {_dur(hb.next_due - s.now)}" if hb.next_due > s.now else "ciclo en curso"
            bot = paint("● VIVO", "green", "bold") + f" · último ciclo hace {_dur(s.now - hb.ts)} · {nxt}"
        out.append(f" Bot: {bot}")
        out.append(paint(f"      último resultado: {hb.status} · {hb.detail}", "dim"))
    elif hb is None:
        out.append(" Bot: " + paint("○ sin latido", "yellow") + " (no está corriendo en modo continuo)")
    else:
        out.append(" Bot: " + paint(f"? latido {hb}", "yellow"))
    out.append(
        " Kill switch: "
        + (paint("ACTIVADO (no compra)", "red", "bold") if s.killed else paint("desactivado", "green"))
    )
    if s.note:
        out.append(paint(f" Nota: {s.note}", "yellow"))
    out.append(rule)
    # precio
    if s.price is None:
        out.append(" Precio: sin datos")
    else:
        move = ""
        if s.last_close:
            chg = (s.price - s.last_close) / s.last_close
            move = f"  {paint(_pct(chg), pnl_color(chg))} vs último cierre ({s.last_close:,.2f})"
        tag = "en vivo" if s.live else "último cierre (sin precio en vivo)"
        out.append(f" Precio: {paint(f'{s.price:,.2f}', 'bold')} {st.symbol.split('/')[-1]} · {tag}{move}")
        if s.closes:
            n = min(len(s.closes), SPARK_CANDLES)
            span = timedelta(seconds=tf_seconds(st.timeframe) * n)
            out.append(
                f" {paint(sparkline(s.closes), 'cyan')}"
                f"  mín {min(s.closes[-n:]):,.2f} · máx {max(s.closes[-n:]):,.2f}"
            )
            out.append(paint(f" (últimas {n} velas de {st.timeframe} ≈ {_dur(span)})", "dim"))
    out.append(rule)
    # cuenta
    if s.cash is None:
        out.append(" Cuenta: el bot todavía no creó su estado (data/paper.json)")
    else:
        day = ""
        if s.day_start is not None and s.equity is not None:
            d = s.equity - s.day_start
            day = f" · hoy {paint(_signed(d), pnl_color(d))}"
        out.append(f" Capital: {_money(s.equity)} · efectivo {_money(s.cash)}{day}")
    # posición
    pos = s.position
    if pos is None:
        out.append(" Posición: " + paint("ninguna", "dim") + " · esperando señal de COMPRA")
    else:
        out.append(
            f" Posición: {paint('ABIERTA', 'bold')} {pos.qty} {pos.symbol} · entrada {pos.entry_price:,.2f}"
            f" · desde {pos.entry_candle_ts:%Y-%m-%d %H:%M} UTC"
        )
        if s.price is not None:
            value = pos.qty * s.price
            pnl = value - pos.cost
            out.append(
                f"   P&L latente: {paint(_signed(pnl), pnl_color(pnl))}"
                f" ({paint(_pct(pnl / pos.cost), pnl_color(pnl))})"
                f" · valor {value:,.2f}"
            )
            stop = pos.entry_price * (1 - st.stop_loss_pct)
            dist = (s.price - stop) / s.price
            warn = "red" if dist < Decimal("0.01") else "yellow" if dist < Decimal("0.02") else "dim"
            out.append(
                f"   Stop-loss en {stop:,.2f} (-{st.stop_loss_pct * 100:.2f}% de la entrada) · "
                + paint(f"a {dist * 100:.2f}% del precio", warn)
            )
    out.append(rule)
    # señales
    in_pos = pos is not None
    out.append(" SEÑALES (valor actual del indicador vs lo que pide la regla)")
    for title, checks, active in (("COMPRA", s.entry, not in_pos), ("VENTA ", s.exit, in_pos)):
        state = (
            "se evalúa ahora"
            if active
            else "inactiva" + (" (ya hay posición)" if title == "COMPRA" else " (no hay posición)")
        )
        head = paint(f" {title}", "bold") if active else paint(f" {title}", "dim")
        out.append(f"{head} · {state}")
        for c in checks:
            pad = "   " + "  " * c.depth
            mark = paint("✓", "green") if c.met else paint("✗", "red")
            if not active:
                mark = paint("·", "dim")
            vals = ""
            if c.left is not None or c.right is not None:
                a = "sin datos" if c.left is None else f"{c.left:,.2f}"
                b = "sin datos" if c.right is None else f"{c.right:,.2f}"
                vals = paint(f"   [{a} vs {b}]", "dim")
            out.append(f"{pad}{mark} {c.text}{vals}")
    out.append(rule)
    # operaciones y eventos
    out.append(" ÚLTIMAS OPERACIONES")
    if not s.fills:
        out.append(paint("   (ninguna todavía)", "dim"))
    for f in s.fills:
        res_pnl = "" if f.pnl is None else "  P&L " + paint(_signed(f.pnl), pnl_color(f.pnl))
        side = paint("COMPRA", "green") if f.side.value == "buy" else paint("VENTA ", "red")
        out.append(f"   {f.ts:%m-%d %H:%M} {side} {f.qty} @ {f.price:,.2f}{res_pnl}")
    out.append(" ÚLTIMOS EVENTOS (auditoría)")
    if not s.events:
        out.append(paint("   (sin eventos)", "dim"))
    for hora, action, subject, result in s.events:
        res = paint(result, "green" if result == "ok" else "yellow")
        out.append(f"   {hora} {action:<22} {subject[:26]:<26} {res}")
    out.append(rule)
    out.append(
        paint(
            " Solo lectura: este panel no envía órdenes. Ctrl+C para salir. No es asesoría financiera.", "dim"
        )
    )
    return "\n".join(out)


# --- bucle -------------------------------------------------------------------------------------


def run_watch(
    snapshot: Callable[[], Snapshot],
    out: Callable[[str], object],
    *,
    interval: float,
    once: bool,
    sleep: Callable[[float], object],
    color: bool,
    clear: bool,
    stop: Callable[[], bool] = lambda: False,
) -> int:
    """Refresca hasta Ctrl+C. Si un refresco falla (red, archivo a medias) lo muestra y reintenta."""
    last = ""
    while True:
        try:
            last = render(snapshot(), color)
            err = ""
        except Exception as e:  # noqa: BLE001 - un panel no debe caerse por un fallo pasajero
            err = f"\n No se pudo actualizar: {e}"
            if once:
                out(f"Error: {e}")
                return 3
        out((CLEAR if clear else "") + last + err)
        if once or stop():
            return 0
        sleep(interval)
