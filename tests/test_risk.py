from decimal import Decimal as D

import pytest

from tradebot.models import Order, RiskLimits, Side
from tradebot.risk import RiskEngine

EQUITY = D("10000")


@pytest.fixture
def engine() -> RiskEngine:
    limits = RiskLimits(max_order_pct=D("0.02"), max_daily_loss_pct=D("0.03"), max_open_positions=2)
    return RiskEngine(limits, EQUITY)


def buy(qty: str = "0.003", price: str = "60000") -> Order:
    return Order("BTC/USDT", Side.BUY, D(qty), D(price))


def test_allows_order_within_limits(engine):
    assert engine.check(buy(), open_positions=0, daily_pnl=D("0")).allowed  # notional 180 <= 200


def test_rejects_oversized_order(engine):
    d = engine.check(buy(qty="0.004"), open_positions=0, daily_pnl=D("0"))  # notional 240 > 200
    assert not d.allowed and "tamaño" in d.reason


def test_rejects_when_daily_loss_reached(engine):
    d = engine.check(buy(), open_positions=0, daily_pnl=D("-300"))
    assert not d.allowed and "pérdida diaria" in d.reason


def test_rejects_when_max_positions_open(engine):
    d = engine.check(buy(), open_positions=2, daily_pnl=D("0"))
    assert not d.allowed and "posiciones" in d.reason


def test_kill_switch_blocks_buys_but_not_sells(engine):
    engine.kill()
    assert not engine.check(buy(), 0, D("0")).allowed
    assert engine.check(Order("BTC/USDT", Side.SELL, D("1"), D("60000")), 0, D("0")).allowed


@pytest.mark.parametrize("qty,price", [("0", "60000"), ("-1", "60000"), ("1", "0")])
def test_rejects_invalid_orders(engine, qty, price):
    assert not engine.check(buy(qty, price), 0, D("0")).allowed


@pytest.mark.parametrize(
    "kw", [{"max_order_pct": D("0")}, {"max_daily_loss_pct": D("1.5")}, {"max_open_positions": 0}]
)
def test_invalid_limits_raise(kw):
    with pytest.raises(ValueError):
        RiskLimits(**kw)


# --- kill switch persistente ---------------------------------------------------------------------


def test_kill_switch_survives_restart(tmp_path):
    state = tmp_path / "state" / "kill.json"
    first = RiskEngine(RiskLimits(), EQUITY, state)
    first.kill("prueba", "camilo")
    again = RiskEngine(RiskLimits(), EQUITY, state)  # "reinicio": proceso nuevo, mismo archivo
    assert again.killed and not again.check(buy(), 0, D("0")).allowed


def test_kill_from_another_process_stops_a_running_engine(tmp_path):
    state = tmp_path / "kill.json"
    running = RiskEngine(RiskLimits(), EQUITY, state)
    assert running.check(buy(), 0, D("0")).allowed
    RiskEngine(RiskLimits(), EQUITY, state).kill("emergencia", "camilo")  # otra consola
    assert not running.check(buy(), 0, D("0")).allowed


def test_release_needs_persistence_and_reopens(tmp_path):
    state = tmp_path / "kill.json"
    e = RiskEngine(RiskLimits(), EQUITY, state)
    e.kill()
    e.release("camilo")
    assert not e.killed and not RiskEngine(RiskLimits(), EQUITY, state).killed


def test_unreadable_state_file_fails_closed(tmp_path):
    state = tmp_path / "kill.json"
    state.write_text("{ esto no es json")
    e = RiskEngine(RiskLimits(), EQUITY, state)
    assert e.killed and not e.check(buy(), 0, D("0")).allowed
    state.write_text("[]")
    assert e.killed


def test_kill_takes_effect_even_if_disk_write_fails(tmp_path):
    blocker = tmp_path / "bloqueado"
    blocker.write_text("soy un archivo, no una carpeta")
    e = RiskEngine(RiskLimits(), EQUITY, blocker / "kill.json")
    assert e.kill() is False  # no puede crear la carpeta: avisa de que NO se persistió
    assert e.killed  # pero igualmente queda activado en memoria


def test_kill_reports_persistence(tmp_path):
    assert RiskEngine(RiskLimits(), EQUITY, tmp_path / "k.json").kill() is True
    assert RiskEngine(RiskLimits(), EQUITY).kill() is False  # sin archivo configurado


def test_release_fails_loudly_if_it_cannot_persist(tmp_path):
    blocker = tmp_path / "bloqueado"
    blocker.write_text("x")
    e = RiskEngine(RiskLimits(), EQUITY, blocker / "kill.json")
    e.kill()
    with pytest.raises(OSError):
        e.release("camilo")
    assert e.killed  # sigue activado


def test_equity_is_read_only_but_settable_via_method(engine):
    with pytest.raises(AttributeError):
        engine.equity = D("1")  # type: ignore[misc]
    engine.set_equity(D("500"))
    assert engine.equity == D("500")
