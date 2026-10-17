import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradebot.cli import main
from tradebot.execution.paper import PaperBroker
from tradebot.models import Candle, Order, RiskLimits, Side
from tradebot.risk import RiskEngine
from tradebot.strategy import Strategy, StrategyStore
from tradebot.strategy.engine import explain
from tradebot.watch import (
    CLEAR,
    Heartbeat,
    collect,
    read_events,
    read_heartbeat,
    render,
    run_watch,
    sparkline,
)

KEY = "k" * 40
NOW = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)
SPEC = {
    "name": "siempre",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "size_pct": "0.02",
    "stop_loss_pct": "0.03",
    "entry": {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": "<", "right": 30},
    "exit": {
        "type": "any",
        "of": [
            {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": ">", "right": 55},
            {
                "type": "cmp",
                "left": {"kind": "close"},
                "op": "crosses_above",
                "right": {"kind": "sma", "period": 20},
            },
        ],
    },
}


def candles(n: int = 700) -> list[Candle]:
    t0 = NOW - timedelta(hours=n)
    out = []
    for i in range(n):
        p = D(str(round(100 + 5 * math.sin(i / 7), 4)))
        out.append(Candle(t0 + timedelta(hours=i), p, p, p, p, D(1)))
    return out


class Market:
    def __init__(self, data: list[Candle] | None = None) -> None:
        self.data = candles() if data is None else data

    def candles(self, symbol, timeframe, limit):
        return self.data[-limit:]


@pytest.fixture
def strategy() -> Strategy:
    return Strategy.model_validate(SPEC)


@pytest.fixture
def paths(tmp_path):
    return {
        "state": tmp_path / "paper.json",
        "kill_state": tmp_path / "kill.json",
        "heartbeat": tmp_path / "hb.json",
        "audit": tmp_path / "audit.jsonl",
    }


def snap(strategy, paths, price_fn=None, market=None):
    return collect(strategy, "", market or Market(), price_fn, now=NOW, **paths)


def open_position(paths):
    b = PaperBroker(paths["state"])
    t = NOW - timedelta(hours=5)
    b.submit(Order("BTC/USDT", Side.BUY, D("1"), D("100"), t), "c1", entry_candle_ts=t)
    return b


# --- piezas ---------------------------------------------------------------------------------------


def test_sparkline_shapes():
    assert sparkline([]) == ""
    assert sparkline([D(5), D(5)]) == "▄▄"
    s = sparkline([D(1), D(2), D(3)])
    assert s[0] == "▁" and s[-1] == "█" and len(s) == 3
    assert len(sparkline([D(i) for i in range(100)], width=10)) == 10


def test_explain_gives_current_values_and_groups(strategy):
    checks = explain(strategy.exit, candles())
    assert checks[0].text.startswith("ALGUNA") and checks[0].depth == 0
    assert [c.depth for c in checks[1:]] == [1, 1]
    rsi_check = checks[1]
    assert rsi_check.left is not None and rsi_check.right == D(55) and rsi_check.met == (rsi_check.left > 55)
    assert explain(strategy.entry, [])[0].left is None  # sin datos: no inventa valores


def test_read_heartbeat_cases(tmp_path):
    p = tmp_path / "hb.json"
    assert read_heartbeat(p) is None
    p.write_text("no es json")
    assert read_heartbeat(p) == "ilegible"
    p.write_text(
        json.dumps(
            {"ts": NOW.isoformat(), "status": "ok", "detail": "d", "failures": 0, "next_due": NOW.isoformat()}
        )
    )
    assert isinstance(read_heartbeat(p), Heartbeat)


def test_read_events_tolerates_garbage_and_missing_file(tmp_path):
    p = tmp_path / "audit.jsonl"
    assert read_events(p) == []
    rows = [
        {"ts": f"2026-10-17T16:00:0{i}+00:00", "action": f"a{i}", "subject": "s", "result": "ok"}
        for i in range(8)
    ]
    p.write_text("basura\n" + "\n".join(json.dumps(r) for r in rows) + "\n{roto\n")
    ev = read_events(p, n=3)
    assert [e[1] for e in ev] == ["a5", "a6", "a7"] and ev[0][0] == "16:00:05"
    # archivo grande: solo lee la cola y descarta la línea cortada
    assert read_events(p, n=2, max_bytes=120)[-1][1] == "a7"


# --- collect + render -----------------------------------------------------------------------------


def test_render_without_any_state(strategy, paths):
    text = render(snap(strategy, paths), color=False)
    for fragment in (
        "siempre v1",
        "BTC/USDT 1h",
        "sin latido",
        "desactivado",
        "todavía no creó su estado",
        "Posición: ninguna",
        "COMPRA · se evalúa ahora",
        "VENTA  · inactiva (no hay posición)",
        "(ninguna todavía)",
        "Solo lectura",
    ):
        assert fragment in text
    assert "\x1b[" not in text


def test_price_falls_back_to_last_close_when_live_price_fails(strategy, paths):
    def boom(_symbol):
        raise RuntimeError("sin red")

    s = snap(strategy, paths, price_fn=boom)
    assert s.live is False and s.price == s.last_close
    assert "último cierre (sin precio en vivo)" in render(s, color=False)
    live = snap(strategy, paths, price_fn=lambda _s: D("123.45"))
    assert live.live and "123.45" in render(live, color=False) and "en vivo" in render(live, color=False)
    assert live.closes[-1] == D("123.45")


def test_render_open_position_shows_pnl_and_stop_distance(strategy, paths):
    open_position(paths)
    text = render(snap(strategy, paths, price_fn=lambda _s: D("110")), color=False)
    assert "ABIERTA" in text and "P&L latente: +" in text
    assert "Stop-loss en" in text and "VENTA  · se evalúa ahora" in text
    assert "COMPRA · inactiva (ya hay posición)" in text
    assert "COMPRA" in text and "@" in text  # la operación de entrada aparece en el historial


def test_stop_distance_warning_and_losses(strategy, paths):
    open_position(paths)
    text = render(snap(strategy, paths, price_fn=lambda _s: D("97.5")), color=False)  # casi en el stop
    assert "P&L latente: -" in text


def test_heartbeat_states(strategy, paths):
    def write(ts, due):
        paths["heartbeat"].write_text(
            json.dumps(
                {
                    "ts": ts.isoformat(),
                    "status": "ok",
                    "detail": "hold: sin señal",
                    "failures": 0,
                    "next_due": due.isoformat(),
                }
            )
        )

    write(NOW - timedelta(minutes=5), NOW + timedelta(minutes=55))
    assert "● VIVO" in render(snap(strategy, paths), color=False)
    write(NOW - timedelta(hours=3), NOW - timedelta(hours=2))
    assert "SIN LATIDO" in render(snap(strategy, paths), color=False)
    write(NOW - timedelta(minutes=70), NOW - timedelta(seconds=30))  # dentro de la gracia
    assert "● VIVO" in render(snap(strategy, paths), color=False)
    paths["heartbeat"].write_text("roto")
    assert "latido ilegible" in render(snap(strategy, paths), color=False)


def test_kill_switch_and_note_are_visible(strategy, paths):
    RiskEngine(RiskLimits(), D(0), paths["kill_state"]).kill("prueba", "camilo")
    s = collect(strategy, "ojo con esto", Market(), None, now=NOW, **paths)
    text = render(s, color=False)
    assert "ACTIVADO (no compra)" in text and "Nota: ojo con esto" in text


def test_events_and_day_pnl_are_shown(strategy, paths):
    paths["audit"].write_text(
        json.dumps({"ts": "2026-10-17T15:59:01+00:00", "action": "bot.start", "subject": "x", "result": "ok"})
        + "\n"
    )
    b = open_position(paths)
    b.day_start_equity(NOW, D("100"))  # fija el capital de inicio del día
    text = render(snap(strategy, paths, price_fn=lambda _s: D("101")), color=False)
    assert "15:59:01 bot.start" in text and "hoy " in text


def test_no_data_at_all(strategy, paths):
    text = render(snap(strategy, paths, market=Market([])), color=False)
    assert "Precio: sin datos" in text and "sin datos" in text


def test_color_is_optional(strategy, paths):
    s = snap(strategy, paths, price_fn=lambda _s: D("100"))
    assert "\x1b[" in render(s, color=True) and "\x1b[" not in render(s, color=False)


# --- bucle ----------------------------------------------------------------------------------------


def test_run_watch_once_and_error_handling(strategy, paths):
    lines: list[str] = []
    ok = run_watch(
        lambda: snap(strategy, paths),
        lines.append,
        interval=1,
        once=True,
        sleep=lambda _s: None,
        color=False,
        clear=False,
    )
    assert ok == 0 and "siempre" in lines[0] and not lines[0].startswith(CLEAR)

    def boom():
        raise RuntimeError("sin red")

    lines.clear()
    assert (
        run_watch(boom, lines.append, interval=1, once=True, sleep=lambda _s: None, color=False, clear=False)
        == 3
    )
    assert "sin red" in lines[0]


def test_run_watch_keeps_last_screen_when_a_refresh_fails(strategy, paths):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("sin red")
        return snap(strategy, paths)

    lines: list[str] = []
    sleeps: list[float] = []
    run_watch(
        flaky,
        lines.append,
        interval=7,
        once=False,
        sleep=sleeps.append,
        color=False,
        clear=True,
        stop=lambda: calls["n"] >= 3,
    )
    assert len(lines) == 3 and all(ln.startswith(CLEAR) for ln in lines)
    assert (
        "No se pudo actualizar: sin red" in lines[1] and "siempre" in lines[1]
    )  # conserva la pantalla anterior
    assert "No se pudo" not in lines[2] and sleeps == [7, 7]


# --- comando --------------------------------------------------------------------------------------


class FreshSource:
    def fetch(self, symbol, timeframe, since):
        t = since.replace(minute=0, second=0, microsecond=0)
        end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        out = []
        while t <= end:
            p = D(str(round(100 + 5 * math.sin(t.timestamp() / 3600 / 7), 4)))
            out.append(Candle(t, p, p, p, p, D(1)))
            t += timedelta(hours=1)
        return out


class LiveSource(FreshSource):
    def last_price(self, symbol):
        return D("4242.42")


@pytest.fixture
def cli(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEBOT_KEY", KEY)
    d = tmp_path / "s"
    store = StrategyStore(d, key=KEY.encode())
    store.save(Strategy.model_validate(SPEC))
    args = [
        "--audit",
        str(tmp_path / "audit.jsonl"),
        "watch",
        "siempre",
        "--once",
        "--strategies-dir",
        str(d),
        "--state",
        str(tmp_path / "paper.json"),
        "--kill-state",
        str(tmp_path / "kill.json"),
        "--heartbeat",
        str(tmp_path / "hb.json"),
    ]
    return args, store


def run_cli(args, source):
    lines: list[str] = []
    return main(args, source=source, out=lines.append), "\n".join(lines)


def test_watch_command_unapproved_strategy_is_flagged(cli):
    args, _ = cli
    code, text = run_cli(args, FreshSource())
    assert code == 0 and "siempre v1" in text and "Solo lectura" in text
    assert "ATENCIÓN, aprobación no válida" in text


def test_watch_command_approved_strategy_has_no_warning_and_shows_live_price(cli):
    args, store = cli
    store.approve("siempre", 1, "camilo")
    code, text = run_cli(args, LiveSource())
    assert code == 0 and "ATENCIÓN" not in text and "Nota:" not in text
    assert "4,242.42" in text and "en vivo" in text


def test_watch_command_without_key_still_works_and_says_so(cli, monkeypatch):
    args, _ = cli
    monkeypatch.delenv("TRADEBOT_KEY")
    code, text = run_cli(args, FreshSource())
    assert code == 0 and "sin TRADEBOT_KEY" in text


def test_watch_command_errors(cli):
    args, _ = cli
    assert run_cli([*args, "--interval", "0"], FreshSource())[0] == 2
    bad = [a if a != "siempre" else "no-existe" for a in args]
    code, text = run_cli(bad, FreshSource())
    assert code == 2 and "no existe" in text

    class Broken:
        def fetch(self, symbol, timeframe, since):
            raise RuntimeError("sin red")

    code, text = run_cli(args, Broken())
    assert code == 3 and "sin red" in text


def test_watch_loop_exits_cleanly_on_ctrl_c(cli, monkeypatch):
    from tradebot import cli as cli_mod

    args, _ = cli

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mod, "run_watch", interrupted)
    code, text = run_cli([a for a in args if a != "--once"], FreshSource())
    assert code == 0 and "Panel cerrado" in text
