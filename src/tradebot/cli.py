import argparse
import getpass
import json
import logging
import math
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tradebot.audit import AuditError, AuditLog
from tradebot.backtest import run
from tradebot.config import DEFAULT_EQUITY, FEE_PCT, SLIPPAGE_PCT
from tradebot.data import CachedMarketData, CandleCache, CcxtSource
from tradebot.execution import Bot, BrokerError, PaperBroker, run_loop
from tradebot.integrity import MissingKeyError, load_key, sha256_hex
from tradebot.llm import AnthropicClient, LlmClient, LlmError, ProposalError, propose
from tradebot.logging_setup import configure_logging, trace
from tradebot.models import RiskLimits
from tradebot.ports import CandleSource
from tradebot.risk import RiskEngine
from tradebot.strategy import Strategy, StrategyStore
from tradebot.strategy.describe import describe
from tradebot.strategy.engine import min_candles
from tradebot.strategy.store import ApprovalError
from tradebot.timeframe import tf_seconds
from tradebot.watch import Snapshot, collect, run_watch

_LIM = RiskLimits()
log = logging.getLogger("tradebot.cli")
DEFAULT_STATE = "data/kill.json"
DEFAULT_AUDIT = "data/audit.jsonl"
DEFAULT_PAPER = "data/paper.json"
DEFAULT_HEARTBEAT = "data/heartbeat.json"
EXIT_INTEGRITY = 4


def _dec(s: str) -> Decimal:
    try:
        return Decimal(s)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"número inválido: {s!r}") from None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tradebot", description="Bot de trading: herramientas de consola")
    p.add_argument(
        "--log-level",
        default=os.environ.get("TRADEBOT_LOG_LEVEL", "WARNING"),
        help="DEBUG, INFO, WARNING (por defecto) o ERROR; también TRADEBOT_LOG_LEVEL",
    )
    p.add_argument("--audit", default=DEFAULT_AUDIT, help=f"registro de auditoría ({DEFAULT_AUDIT})")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="simula una estrategia sobre velas históricas")
    b.add_argument("strategy", help="ruta a un .json, o nombre guardado en --strategies-dir")
    b.add_argument("--days", type=int, default=365, help="días de historia a simular (365)")
    b.add_argument("--equity", type=_dec, default=DEFAULT_EQUITY, help="capital inicial (10000)")
    b.add_argument("--fee", type=_dec, default=FEE_PCT, help="comisión por operación, 0.001 = 0.1%%")
    b.add_argument("--slippage", type=_dec, default=SLIPPAGE_PCT, help="deslizamiento, 0.0005 = 0.05%%")
    b.add_argument("--max-order-pct", type=_dec, default=_LIM.max_order_pct, help="tope por orden")
    b.add_argument("--max-daily-loss-pct", type=_dec, default=_LIM.max_daily_loss_pct, help="pérdida diaria")
    b.add_argument("--exchange", default="binance", help="exchange de CCXT para las velas (binance)")
    b.add_argument("--db", default="data/candles.db", help="caché local de velas (data/candles.db)")
    b.add_argument("--strategies-dir", default="strategies", help="carpeta de estrategias guardadas")

    a = sub.add_parser("approve", help="aprueba (con firma) una versión guardada de una estrategia")
    a.add_argument("name")
    a.add_argument("version", type=int)
    a.add_argument("--user", default=None, help="quién aprueba (por defecto, el usuario del sistema)")
    a.add_argument("--strategies-dir", default="strategies")

    k = sub.add_parser("kill", help="EMERGENCIA: bloquea nuevas compras (también en un bot ya en marcha)")
    k.add_argument("--reason", default="manual")
    k.add_argument("--state", default=DEFAULT_STATE, help=f"archivo de estado ({DEFAULT_STATE})")

    rel = sub.add_parser("release", help="desactiva el kill switch")
    rel.add_argument("--state", default=DEFAULT_STATE)

    pr = sub.add_parser(
        "propose", help="convierte una directriz en una estrategia (SIN aprobar) usando un LLM"
    )
    pr.add_argument(
        "directive", help='texto de la directriz, entre comillas: "compra cuando el RSI baje de 30..."'
    )
    pr.add_argument("--model", default=None, help="modelo del proveedor (por defecto TRADEBOT_LLM_MODEL)")
    pr.add_argument("--strategies-dir", default="strategies")

    sh = sub.add_parser("show", help="muestra en español qué hace una estrategia guardada y si está aprobada")
    sh.add_argument("name")
    sh.add_argument("--version", type=int, default=None, help="por defecto, la última")
    sh.add_argument("--strategies-dir", default="strategies")

    pp = sub.add_parser(
        "paper", help="opera una estrategia APROBADA en modo paper (simulado) con datos reales"
    )
    pp.add_argument("name", help="nombre de una estrategia aprobada")
    pp.add_argument("--once", action="store_true", help="un solo ciclo y salir (para probar)")
    pp.add_argument(
        "--equity", type=_dec, default=DEFAULT_EQUITY, help="capital inicial (solo al crear el estado)"
    )
    pp.add_argument("--fee", type=_dec, default=FEE_PCT)
    pp.add_argument("--slippage", type=_dec, default=SLIPPAGE_PCT)
    pp.add_argument("--max-order-pct", type=_dec, default=_LIM.max_order_pct)
    pp.add_argument("--max-daily-loss-pct", type=_dec, default=_LIM.max_daily_loss_pct)
    pp.add_argument("--exchange", default="binance")
    pp.add_argument("--db", default="data/candles.db")
    pp.add_argument("--strategies-dir", default="strategies")
    pp.add_argument("--state", default=DEFAULT_PAPER, help=f"estado del broker simulado ({DEFAULT_PAPER})")
    pp.add_argument("--kill-state", default=DEFAULT_STATE)
    pp.add_argument("--heartbeat", default=DEFAULT_HEARTBEAT)

    st = sub.add_parser("status", help="estado del bot: latido, kill switch, efectivo y posición")
    st.add_argument("--state", default=DEFAULT_PAPER)
    st.add_argument("--kill-state", default=DEFAULT_STATE)
    st.add_argument("--heartbeat", default=DEFAULT_HEARTBEAT)

    w = sub.add_parser("watch", help="panel en vivo de SOLO LECTURA: precio, posición, señales y eventos")
    w.add_argument("name", help="nombre de la estrategia que corre el bot")
    w.add_argument("--interval", type=float, default=30.0, help="segundos entre refrescos (30)")
    w.add_argument("--once", action="store_true", help="dibuja una vez y sale")
    w.add_argument("--no-color", action="store_true", help="sin colores")
    w.add_argument("--exchange", default="binance")
    w.add_argument("--db", default=":memory:", help="caché de velas del panel (en memoria por defecto)")
    w.add_argument("--strategies-dir", default="strategies")
    w.add_argument("--state", default=DEFAULT_PAPER)
    w.add_argument("--kill-state", default=DEFAULT_STATE)
    w.add_argument("--heartbeat", default=DEFAULT_HEARTBEAT)

    sub.add_parser("audit-verify", help="verifica la cadena completa del registro de auditoría")
    return p


def _load(ref: str, store_dir: str) -> Strategy:
    p = Path(ref)
    if p.suffix == ".json" or p.is_file():
        return Strategy.model_validate_json(p.read_text(encoding="utf-8"))
    return StrategyStore(store_dir).load(ref)


def main(
    argv: list[str] | None = None,
    source: CandleSource | None = None,
    out: Callable[[str], object] = print,
    llm: LlmClient | None = None,
) -> int:
    a = _parser().parse_args(argv)
    try:
        configure_logging(a.log_level)
    except ValueError as e:
        out(f"Error: {e}")
        return 2
    with trace():
        log.info("comando %s", a.cmd)
        if a.cmd == "backtest":
            return _backtest(a, source, out)
        if a.cmd == "propose":
            return _propose(a, llm, out)
        if a.cmd == "show":
            return _show(a, out)
        if a.cmd == "paper":
            return _paper(a, source, out)
        if a.cmd == "status":
            return _status(a, out)
        if a.cmd == "watch":
            return _watch(a, source, out)
        return _admin(a, out)


def _backtest(a: argparse.Namespace, source: CandleSource | None, out: Callable[[str], object]) -> int:
    try:
        if a.days < 1:
            raise ValueError("--days debe ser al menos 1")
        strategy = _load(a.strategy, a.strategies_dir)
        limits = RiskLimits(max_order_pct=a.max_order_pct, max_daily_loss_pct=a.max_daily_loss_pct)
    except (OSError, ValueError) as e:  # incluye la validación de pydantic
        out(f"Error: {e}")
        return 2

    need = math.ceil(a.days * 86400 / tf_seconds(strategy.timeframe)) + min_candles(strategy)
    try:
        with CandleCache(a.db) as cache:
            md = CachedMarketData(source or CcxtSource(a.exchange), cache)
            candles = md.candles(strategy.symbol, strategy.timeframe, need)
    except Exception as e:  # noqa: BLE001 - frontera del CLI: mensaje claro en vez de traceback
        log.exception("fallo al obtener velas")
        out(f"Error al obtener velas: {e}")
        return 3
    if len(candles) < 2:
        out("Error: el exchange no devolvió velas suficientes")
        return 3
    out(run(strategy, candles, limits, a.equity, a.fee, a.slippage).summary())
    return 0


def _admin(a: argparse.Namespace, out: Callable[[str], object]) -> int:
    """Comandos humanos de control. Todos dejan rastro en la auditoría con el nombre de quien los ejecuta.

    - approve y release EXIGEN clave y auditoría: son los gestos que abren la puerta al trading.
    - kill funciona SIEMPRE, con o sin clave: en una emergencia nada debe impedir frenar.
    """
    actor = getattr(a, "user", None) or getpass.getuser()
    try:
        if a.cmd == "kill":
            return _kill(a, actor, out)
        key = load_key()
        audit = AuditLog(a.audit, key)
        if a.cmd == "audit-verify":
            res = audit.verify()
            if res.ok:
                out(f"Auditoría íntegra: {res.records} registros verificados")
                return 0
            out(f"AUDITORÍA ALTERADA tras {res.records} registros válidos: {res.error}")
            return EXIT_INTEGRITY
        if a.cmd == "approve":
            StrategyStore(a.strategies_dir, key=key, audit=audit).approve(a.name, a.version, actor)
            out(f"Aprobada: {a.name} v{a.version} por {actor}")
            return 0
        RiskEngine(_LIM, Decimal(0), a.state, audit).release(actor)
        out(f"Kill switch liberado por {actor}")
        return 0
    except (OSError, ValueError, MissingKeyError, AuditError) as e:
        out(f"Error: {e}")
        return 2


def _kill(a: argparse.Namespace, actor: str, out: Callable[[str], object]) -> int:
    audit: AuditLog | None = None
    try:
        audit = AuditLog(a.audit, load_key())
    except MissingKeyError:
        out("Aviso: sin TRADEBOT_KEY, el kill switch se activa pero SIN registro de auditoría")
    if not RiskEngine(_LIM, Decimal(0), a.state, audit).kill(a.reason, actor):
        out("ATENCIÓN: no se pudo escribir el estado; solo afecta a este proceso, no al bot en marcha")
        return 3
    out(f"Kill switch ACTIVADO por {actor}: {a.reason}")
    return 0


def _propose(a: argparse.Namespace, llm: LlmClient | None, out: Callable[[str], object]) -> int:
    """Directriz -> estrategia guardada SIN aprobar. El LLM no ejecuta nada: aprobar sigue siendo humano."""
    actor = getpass.getuser()
    try:
        key = load_key()  # la auditoría es obligatoria también aquí
        audit = AuditLog(a.audit, key)
        digest = sha256_hex(a.directive.encode("utf-8"))
        try:
            proposal = propose(a.directive, llm or AnthropicClient(a.model))
        except (ProposalError, LlmError) as e:
            audit.append(actor, "llm.propose", "-", "error", {"sha256": digest, "reason": str(e)[:300]})
            out(f"No se pudo generar la estrategia: {e}")
            return 3
        saved = StrategyStore(a.strategies_dir, key=key, audit=audit).save(proposal.strategy)
        details = {"sha256": digest, "preview": a.directive[:200], "attempts": proposal.attempts}
        audit.append(actor, "llm.propose", f"{saved.name} v{saved.version}", "ok", details)
        out(describe(saved))
        out("")
        out(f"Guardada como {saved.name} v{saved.version}, SIN APROBAR.")
        out("Léela con atención: la propuso un LLM y la aprobación es tu único control.")
        out(f"Pruébala:  tradebot backtest {saved.name} --days 365")
        out(f"Si estás de acuerdo:  tradebot approve {saved.name} {saved.version}")
        return 0
    except (OSError, ValueError, MissingKeyError, AuditError) as e:
        out(f"Error: {e}")
        return 2


def _show(a: argparse.Namespace, out: Callable[[str], object]) -> int:
    try:
        try:
            key: bytes | None = load_key()
        except MissingKeyError:
            key = None
        store = StrategyStore(a.strategies_dir, key=key)
        s = store.load(a.name, a.version)
    except (OSError, ValueError) as e:
        out(f"Error: {e}")
        return 2
    out(describe(s))
    if key is None:
        out("Aprobación: desconocida (define TRADEBOT_KEY para verificarla)")
    else:
        out("Aprobación: VÁLIDA" if store.is_approved(s.name, s.version) else "Aprobación: NO aprobada")
    return 0


def _paper(a: argparse.Namespace, source: CandleSource | None, out: Callable[[str], object]) -> int:
    """Paper trading: solo estrategias APROBADAS, con riesgo, auditoría y kill switch persistente."""
    mode = os.environ.get("TRADING_MODE", "paper").lower()
    if mode != "paper":
        out(f"Error: TRADING_MODE={mode!r}. Solo existe el modo paper; el modo real no está implementado.")
        return 2
    actor = getpass.getuser()
    try:
        key = load_key()
        audit = AuditLog(a.audit, key)
        store = StrategyStore(a.strategies_dir, key=key, audit=audit)
        strategy = store.load_approved(a.name)
        limits = RiskLimits(max_order_pct=a.max_order_pct, max_daily_loss_pct=a.max_daily_loss_pct)
        broker = PaperBroker(a.state, a.equity, a.fee, a.slippage)
        broker.validate_for(strategy.symbol)
        risk = RiskEngine(limits, broker.cash, a.kill_state, audit)
        audit.append(actor, "bot.start", f"{strategy.name} v{strategy.version}", "ok", {"mode": "paper"})
    except (OSError, ValueError, MissingKeyError, AuditError, ApprovalError, BrokerError) as e:
        out(f"Error: {e}")
        return 2
    try:
        with CandleCache(a.db) as cache:
            market = CachedMarketData(source or CcxtSource(a.exchange), cache)

            def still_approved() -> object:
                return store.check_approval(strategy.name, strategy.version)

            bot = Bot(strategy, market, broker, risk, audit, approval_check=still_approved)
            if a.once:
                r = bot.tick()
                out(f"{r.action}: {r.reason}")
                return 0
            out(f"Bot en marcha: {strategy.name} v{strategy.version} (paper). Ctrl+C para detener.")
            try:
                return run_loop(bot, risk, audit, Path(a.heartbeat), lambda s: time.sleep(s))
            except KeyboardInterrupt:
                audit.append(actor, "bot.stop", strategy.name, "ok", {})
                out("Bot detenido.")
                return 0
    except Exception as e:  # noqa: BLE001 - frontera del CLI: datos/red/broker -> mensaje claro
        log.exception("fallo en paper")
        out(f"Error: {e}")
        return 3


def _status(a: argparse.Namespace, out: Callable[[str], object]) -> int:
    now = datetime.now(UTC)
    try:
        hb = json.loads(Path(a.heartbeat).read_text(encoding="utf-8"))
        age = (now - datetime.fromisoformat(hb["ts"])).total_seconds()
        late = now > datetime.fromisoformat(hb["next_due"]) + timedelta(seconds=120)
        flag = "  ¡POSIBLEMENTE CAÍDO: no late a tiempo!" if late else ""
        out(f"Latido: hace {age / 60:.0f} min · {hb['status']} · {hb['detail']}{flag}")
    except FileNotFoundError:
        out("Latido: ninguno (el bot no ha corrido en modo continuo)")
    except (OSError, ValueError, KeyError):
        out("Latido: ilegible")
    killed = RiskEngine(_LIM, Decimal(0), a.kill_state).killed
    out("Kill switch: ACTIVADO (no compra)" if killed else "Kill switch: desactivado")
    try:
        broker = PaperBroker(a.state) if Path(a.state).exists() else None
    except BrokerError as e:
        out(f"Estado del broker: {e}")
        return 3
    if broker is None:
        out("Broker paper: sin estado todavía")
        return 0
    pos = broker.position
    held = "ninguna" if pos is None else f"{pos.qty} {pos.symbol} desde {pos.entry_price:.2f}"
    out(f"Efectivo: {broker.cash:,.2f} · Posición: {held} · Órdenes ejecutadas: {len(broker.fills)}")
    return 0


def _watch(a: argparse.Namespace, source: CandleSource | None, out: Callable[[str], object]) -> int:
    """Panel de solo lectura. No necesita clave; si hay TRADEBOT_KEY, además comprueba la aprobación."""
    if a.interval < 1:
        out("Error: --interval debe ser al menos 1 segundo")
        return 2
    note = ""
    try:
        store = StrategyStore(a.strategies_dir)
        try:
            strategy = StrategyStore(a.strategies_dir, key=load_key()).load_approved(a.name)
        except MissingKeyError:
            strategy = store.load(a.name)
            note = "sin TRADEBOT_KEY: no se verificó la aprobación (se muestra la versión más reciente)"
        except ApprovalError as e:
            strategy = store.load(a.name)
            note = f"ATENCIÓN, aprobación no válida: {e}"
    except (OSError, ValueError) as e:
        out(f"Error: {e}")
        return 2
    tty = bool(getattr(sys.stdout, "isatty", lambda: False)()) and not a.once
    if tty:
        os.system("")  # noqa: S605,S607 - activa los colores ANSI en la consola de Windows 10+
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    try:
        with CandleCache(a.db) as cache:
            src = source or CcxtSource(a.exchange)
            market = CachedMarketData(src, cache)
            price_fn = getattr(src, "last_price", None)

            def snap() -> Snapshot:
                return collect(
                    strategy,
                    note,
                    market,
                    price_fn,
                    state=a.state,
                    kill_state=a.kill_state,
                    heartbeat=a.heartbeat,
                    audit=a.audit,
                )

            try:
                return run_watch(
                    snap,
                    out,
                    interval=a.interval,
                    once=a.once,
                    sleep=time.sleep,
                    color=tty and not a.no_color,
                    clear=tty,
                )
            except KeyboardInterrupt:
                out("\nPanel cerrado.")
                return 0
    except Exception as e:  # noqa: BLE001 - frontera del CLI
        log.exception("fallo en watch")
        out(f"Error: {e}")
        return 3
