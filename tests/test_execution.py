import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradebot.audit import AuditLog
from tradebot.data.validate import check_candles
from tradebot.execution import Bot, BrokerError, PaperBroker, run_loop, seconds_until_next_close
from tradebot.models import Candle, Order, RiskLimits, Side
from tradebot.risk import RiskEngine
from tradebot.strategy import Strategy, StrategyStore
from tradebot.strategy.store import ApprovalError

BASE = datetime(2026, 3, 1, tzinfo=UTC)
H = timedelta(hours=1)
KEY = b"k" * 40


def candle(i: int, o=100, h=None, lo=None, c=None) -> Candle:
    c = o if c is None else c
    h = max(o, c) if h is None else h
    lo = min(o, c) if lo is None else lo
    return Candle(BASE + H * i, D(str(o)), D(str(h)), D(str(lo)), D(str(c)), D(1))


class Market:
    """Mercado falso: las velas se van añadiendo desde el test."""

    def __init__(self, *rows: Candle) -> None:
        self.rows = list(rows)

    def candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        return self.rows[-limit:]

    @property
    def now(self) -> datetime:
        return self.rows[-1].ts + H + timedelta(seconds=30)  # justo después de cerrar la última vela


def spec(entry=None, exit_=None, stop="0.03") -> Strategy:
    always = {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 0}
    never = {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 100000}
    return Strategy.model_validate(
        {
            "name": "t",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "size_pct": "0.02",
            "stop_loss_pct": stop,
            "entry": entry or always,
            "exit": exit_ or never,
        }
    )


def flat(n: int) -> list[Candle]:
    return [candle(i) for i in range(n)]


# --- validación de datos -------------------------------------------------------------------------


def test_clean_recent_data_is_operable():
    m = Market(*flat(5))
    assert check_candles(m.rows, "1h", m.now, 5) is None


def test_data_problems_are_reported():
    ok = flat(5)
    now = Market(*ok).now
    assert "insuficientes" in (check_candles(ok[:2], "1h", now, 5) or "")
    assert "hueco" in (check_candles([*ok[:2], *ok[3:]], "1h", now, 4) or "")
    assert "hueco" in (check_candles([ok[1], ok[0], *ok[2:]], "1h", now, 5) or "")  # desordenadas
    assert "viejos" in (check_candles(ok, "1h", now + 5 * H, 5) or "")
    assert "salto" in (check_candles([*ok[:4], candle(4, o=100, c=300)], "1h", now, 5) or "")
    bad = Candle(ok[-1].ts, D(100), D(90), D(95), D(100), D(1))  # máximo menor que el mínimo
    assert "incoherente" in (check_candles([*ok[:4], bad], "1h", now, 5) or "")
    zero = Candle(ok[-1].ts, D(0), D(0), D(0), D(0), D(1))
    assert check_candles([*ok[:4], zero], "1h", now, 5)  # precio 0: se bloquea (aquí lo ve como salto)
    all_zero = [Candle(BASE + H * i, D(0), D(0), D(0), D(0), D(1)) for i in range(5)]
    assert "incoherente" in (check_candles(all_zero, "1h", now, 5) or "")


# --- broker paper --------------------------------------------------------------------------------


@pytest.fixture
def broker(tmp_path) -> PaperBroker:
    return PaperBroker(tmp_path / "paper.json", D(10000), fee_pct=D("0.001"), slippage_pct=D("0.001"))


def buy(qty="1", px="100") -> Order:
    return Order("BTC/USDT", Side.BUY, D(qty), D(px), BASE)


def sell(qty="1", px="110") -> Order:
    return Order("BTC/USDT", Side.SELL, D(qty), D(px), BASE + H)


def test_buy_then_sell_updates_cash_and_pnl(broker):
    f = broker.submit(buy(), "b1", BASE)
    assert f.price == D("100.1") and broker.position and broker.position.qty == D(1)
    assert broker.cash == D(10000) - D("100.1") - D("0.1001")
    s = broker.submit(sell(), "s1")
    assert broker.position is None and s.pnl is not None and s.pnl > 0
    assert broker.cash == D(10000) + s.pnl


def test_same_client_id_never_executes_twice(broker):
    first = broker.submit(buy(), "dup", BASE)
    cash = broker.cash
    again = broker.submit(buy(), "dup", BASE)
    assert again == first and broker.cash == cash and len(broker.fills) == 1


def test_state_survives_restart(broker, tmp_path):
    broker.submit(buy(), "b1", BASE)
    again = PaperBroker(tmp_path / "paper.json", D(1))  # el capital pasado se ignora: manda el archivo
    assert again.cash == broker.cash and again.position == broker.position and again.has_fill("b1")
    assert again.submit(buy(), "b1", BASE) == broker.fills["b1"]  # idempotente también tras reiniciar


@pytest.mark.parametrize(
    "bad",
    ["no es json", "{}", '{"cash": "-5", "position": null, "fills": {}, "day": null, "day_start": "1"}'],
)
def test_corrupt_or_incoherent_state_refuses_to_start(tmp_path, bad):
    p = tmp_path / "paper.json"
    p.write_text(bad)
    with pytest.raises(BrokerError):
        PaperBroker(p)


def test_invalid_orders_are_rejected(broker):
    with pytest.raises(BrokerError, match="inválidos"):
        broker.submit(buy(qty="0"), "x", BASE)
    with pytest.raises(BrokerError, match="falta la vela"):
        broker.submit(buy(), "x")
    with pytest.raises(BrokerError, match="fondos"):
        broker.submit(buy(qty="1000"), "x", BASE)
    with pytest.raises(BrokerError, match="no hay posición"):
        broker.submit(sell(), "x")
    broker.submit(buy(), "b", BASE)
    with pytest.raises(BrokerError, match="ya hay"):
        broker.submit(buy(), "b2", BASE)
    with pytest.raises(BrokerError, match="completa"):
        broker.submit(sell(qty="0.5"), "s")
    assert broker.cash < D(10000) and broker.position is not None  # nada de lo anterior cambió el estado


def test_failed_save_leaves_memory_untouched(tmp_path):
    blocker = tmp_path / "archivo"
    p = tmp_path / "paper.json"
    b = PaperBroker(p, D(10000))
    b.path = blocker / "paper.json"  # ahora guardar es imposible (el padre es un archivo)
    blocker.write_text("x")
    with pytest.raises(BrokerError, match="guardar"):
        b.submit(buy(), "b", BASE)
    assert b.cash == D(10000) and b.position is None and not b.fills


def test_validate_for_detects_other_symbol(broker):
    broker.submit(buy(), "b", BASE)
    broker.validate_for("BTC/USDT")
    with pytest.raises(BrokerError, match="distinta"):
        broker.validate_for("ETH/USDT")


def test_day_start_equity_rolls_over_each_utc_day(broker, tmp_path):
    t = datetime(2026, 3, 1, 10, tzinfo=UTC)
    assert broker.day_start_equity(t, D(100)) == D(10000)
    broker.submit(buy(), "b", BASE)
    assert broker.day_start_equity(t + H, D(100)) == D(10000)  # mismo día: no cambia
    nxt = broker.day_start_equity(t + 24 * H, D(90))
    assert nxt == broker.equity(D(90)) and nxt != D(10000)
    assert PaperBroker(tmp_path / "paper.json").day_start == nxt  # y queda guardado


# --- el bot --------------------------------------------------------------------------------------


def make_bot(tmp_path, market, strategy=None, approval=None, audit=True):
    broker = PaperBroker(tmp_path / "paper.json", D(10000), D("0.001"), D("0.0005"))
    log = AuditLog(tmp_path / "audit.jsonl", KEY) if audit else None
    risk = RiskEngine(RiskLimits(), broker.cash, tmp_path / "kill.json", log)
    bot = Bot(strategy or spec(), market, broker, risk, log, approval, now=lambda: market.now)
    return bot, broker, risk, log


def actions(tmp_path):
    p = tmp_path / "audit.jsonl"
    return [json.loads(ln)["action"] for ln in p.read_text().splitlines()] if p.exists() else []


def test_signal_buys_once_and_repeating_the_tick_does_not_duplicate(tmp_path):
    m = Market(*flat(5))
    bot, broker, _, _ = make_bot(tmp_path, m)
    r = bot.tick()
    assert r.action == "buy" and broker.position is not None
    qty = broker.position.qty
    assert bot.tick().action == "hold"  # misma vela: no vuelve a comprar
    assert broker.position.qty == qty and len(broker.fills) == 1


def test_restart_on_the_same_candle_does_not_duplicate(tmp_path):
    m = Market(*flat(5))
    bot, _, _, _ = make_bot(tmp_path, m)
    bot.tick()
    bot2, broker2, _, _ = make_bot(tmp_path, m)  # proceso nuevo, mismo estado en disco
    assert bot2.tick().action == "hold" and len(broker2.fills) == 1


def test_exit_signal_sells(tmp_path):
    exit_ = {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 105}
    m = Market(*flat(5))
    bot, broker, _, _ = make_bot(tmp_path, m, spec(exit_=exit_))
    bot.tick()
    m.rows.append(candle(5, o=100, h=108, lo=100, c=108))
    r = bot.tick()
    assert r.action == "sell" and broker.position is None
    assert broker.fills and list(broker.fills.values())[-1].pnl > 0


def test_stop_loss_exits_at_the_stop_price(tmp_path):
    m = Market(*flat(5))
    bot, broker, _, _ = make_bot(tmp_path, m)
    bot.tick()
    entry = broker.position.entry_price
    m.rows.append(candle(5, o=99, h=99, lo=96, c=97))  # toca el 3 % (≈97.05)
    r = bot.tick()
    assert r.action == "stop" and broker.position is None
    sold = [f for f in broker.fills.values() if f.side is Side.SELL][0]
    assert sold.price < entry * D("0.97") + D("0.01") and sold.pnl < 0


def test_stop_loss_on_a_gap_exits_at_the_open(tmp_path):
    m = Market(*flat(5))
    bot, broker, _, _ = make_bot(tmp_path, m)
    bot.tick()
    m.rows.append(candle(5, o=90, h=91, lo=89, c=90))
    assert bot.tick().action == "stop"
    sold = [f for f in broker.fills.values() if f.side is Side.SELL][0]
    assert sold.price < D(90)  # abre en 90 (peor que el stop): sale ahí menos deslizamiento


def test_stops_only_look_at_candles_after_the_entry(tmp_path):
    rows = flat(5)
    rows[2] = candle(2, o=100, h=100, lo=50, c=100)  # una mecha vieja, anterior a la entrada
    m = Market(*rows)
    bot, broker, _, _ = make_bot(tmp_path, m)
    bot.tick()
    assert bot.tick().action == "hold" and broker.position is not None


def test_bad_data_means_no_trading(tmp_path):
    m = Market(*flat(5))
    bot, broker, _, _ = make_bot(tmp_path, m)
    bot.now = lambda: m.now + 10 * H  # los datos quedaron viejos
    r = bot.tick()
    assert r.action == "skip" and "viejos" in r.reason and broker.position is None
    assert "bot.skip" in actions(tmp_path)


def test_unapproved_strategy_cannot_buy_but_stop_loss_still_works(tmp_path):
    state = {"ok": True}

    def check():
        if not state["ok"]:
            raise ApprovalError("la estrategia cambió después de ser aprobada", tamper=True)

    m = Market(*flat(5))
    bot, broker, _, _ = make_bot(tmp_path, m, approval=check)
    bot.tick()
    assert broker.position is not None
    state["ok"] = False  # alguien edita el archivo con el bot en marcha
    m.rows.append(candle(5, o=99, h=99, lo=96, c=97))
    assert bot.tick().action == "stop"  # salir siempre se puede
    m.rows.append(candle(6))
    r = bot.tick()
    assert r.action == "hold" and "sin aprobación" in r.reason and broker.position is None
    bot.tick()
    assert actions(tmp_path).count("strategy.tamper_suspected") == 1  # se avisa una sola vez


def test_kill_switch_blocks_new_buys_from_another_process(tmp_path):
    m = Market(*flat(5))
    bot, broker, risk, _ = make_bot(tmp_path, m)
    RiskEngine(RiskLimits(), D(1), tmp_path / "kill.json").kill("prueba", "camilo")
    r = bot.tick()
    assert r.action == "rejected" and "kill switch" in r.reason and broker.position is None


def test_risk_limits_still_apply_to_the_bot(tmp_path):
    big = spec().model_copy(update={"size_pct": D("0.5")})
    m = Market(*flat(5))
    bot, broker, _, _ = make_bot(tmp_path, m, big)
    r = bot.tick()
    assert r.action == "rejected" and "tamaño" in r.reason and broker.position is None


def test_everything_the_bot_does_is_audited_and_verifies(tmp_path):
    m = Market(*flat(5))
    bot, _, _, log = make_bot(tmp_path, m)
    bot.tick()
    assert "order.check" in actions(tmp_path) and "order.fill" in actions(tmp_path)
    assert log.verify().ok


def test_without_audit_trail_the_bot_does_not_open_positions(tmp_path):
    blocker = tmp_path / "archivo"
    blocker.write_text("x")
    m = Market(*flat(5))
    broker = PaperBroker(tmp_path / "paper.json", D(10000))
    risk = RiskEngine(RiskLimits(), D(10000), audit=AuditLog(blocker / "a.jsonl", KEY))
    bot = Bot(spec(), m, broker, risk, now=lambda: m.now)
    r = bot.tick()
    assert r.action == "rejected" and "auditoría" in r.reason and broker.position is None


def test_broker_rejection_is_reported_not_raised(tmp_path):
    m = Market(*flat(5))
    bot, broker, _, _ = make_bot(tmp_path, m)

    def refuse(*_a, **_k):
        raise BrokerError("el exchange rechazó la orden")

    broker.submit = refuse  # type: ignore[method-assign]
    r = bot.tick()
    assert r.action == "rejected" and "rechazó" in r.reason and broker.position is None
    assert "order.fill" in actions(tmp_path)  # y el rechazo queda auditado


def test_size_is_capped_by_available_cash(tmp_path):
    m = Market(*flat(5))
    bot, broker, _, _ = make_bot(tmp_path, m)
    broker.cash = D("1")
    r = bot.tick()
    assert r.action in {"buy", "rejected"} and broker.cash >= 0  # nunca compra más de lo que hay


# --- bucle ---------------------------------------------------------------------------------------


def test_seconds_until_next_close():
    t = datetime(2026, 3, 1, 10, 59, 30, tzinfo=UTC)
    assert seconds_until_next_close(t, "1h", delay=10) == 40
    assert seconds_until_next_close(datetime(2026, 3, 1, 11, 0, 0, tzinfo=UTC), "1h", delay=0) == 3600


class StubBot:
    def __init__(self, results) -> None:
        self.results, self.strategy = list(results), spec()

    def tick(self):
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_loop_writes_heartbeat_and_sleeps_until_the_next_candle(tmp_path):
    from tradebot.execution import TickResult

    t = datetime(2026, 3, 1, 10, 30, tzinfo=UTC)
    slept, hb = [], tmp_path / "hb.json"
    stops = iter([False, True])
    code = run_loop(
        StubBot([TickResult("hold", "sin señal")]),
        RiskEngine(RiskLimits(), D(1)),
        None,
        hb,
        slept.append,
        stop=lambda: next(stops),
        now=lambda: t,
    )
    beat = json.loads(hb.read_text())
    assert code == 0 and slept == [1810.0] and beat["status"] == "ok" and beat["failures"] == 0
    assert datetime.fromisoformat(beat["next_due"]) == t + timedelta(seconds=1810)


def test_loop_trips_the_kill_switch_after_repeated_failures(tmp_path):
    kill = tmp_path / "kill.json"
    log = AuditLog(tmp_path / "audit.jsonl", KEY)
    risk = RiskEngine(RiskLimits(), D(1), kill, log)
    slept = []
    bot = StubBot([RuntimeError("sin red")] * 3)
    code = run_loop(bot, risk, log, tmp_path / "hb.json", slept.append, max_failures=3)
    assert code == 3 and risk.killed and slept == [10, 20]  # espera creciente entre fallos
    assert json.loads((tmp_path / "hb.json").read_text())["status"] == "error"
    assert "bot.halt" in actions(tmp_path)
    assert json.loads(kill.read_text())["killed"] is True  # y lo ve cualquier otro proceso


def test_a_success_resets_the_failure_count(tmp_path):
    from tradebot.execution import TickResult

    ok = TickResult("hold", "x")
    stops = iter([False] * 4 + [True])
    code = run_loop(
        StubBot([RuntimeError("a"), RuntimeError("b"), ok, RuntimeError("c")]),
        RiskEngine(RiskLimits(), D(1)),
        None,
        tmp_path / "hb.json",
        lambda s: None,
        stop=lambda: next(stops),
        max_failures=3,
    )
    assert code == 0  # nunca llegó a 3 seguidos


def test_stale_approval_helper_is_wired_to_the_store(tmp_path):
    """El chequeo de aprobación del CLI es `store.check_approval`: falla si el archivo cambia."""
    store = StrategyStore(tmp_path / "s", key=KEY)
    saved = store.save(spec())
    store.approve("t", saved.version, "camilo")
    store.check_approval("t", saved.version)
    path = tmp_path / "s" / "t" / f"v{saved.version}.json"
    path.write_text(path.read_text().replace('"0.03"', '"0.9"'))
    with pytest.raises(ApprovalError):
        store.check_approval("t", saved.version)
