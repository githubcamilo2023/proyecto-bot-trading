import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradebot import cli
from tradebot.cli import main
from tradebot.models import Candle
from tradebot.strategy import Strategy, StrategyStore

KEY = "k" * 40
SPEC = {
    "name": "siempre",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "size_pct": "0.02",
    "entry": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 0},
    "exit": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 100000},
}


class FreshSource:
    """Velas horarias sintéticas hasta la última vela cerrada (datos al día)."""

    def __init__(self, hours_old: int = 0) -> None:
        self.hours_old = hours_old

    def fetch(self, symbol, timeframe, since):
        t = since.replace(minute=0, second=0, microsecond=0)
        end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
            hours=1 + self.hours_old
        )
        out = []
        while t <= end:
            p = D(str(round(100 + 5 * math.sin(t.timestamp() / 3600 / 7), 4)))
            out.append(Candle(t, p, p, p, p, D(1)))
            t += timedelta(hours=1)
        return out


class BrokenSource:
    def fetch(self, symbol, timeframe, since):
        raise RuntimeError("sin red")


def run_cli(args, source=None):
    lines: list[str] = []
    return main(args, source=source, out=lines.append), "\n".join(lines)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEBOT_KEY", KEY)
    d = tmp_path / "s"
    store = StrategyStore(d, key=KEY.encode())
    store.save(Strategy.model_validate(SPEC))
    base = ["--audit", str(tmp_path / "audit.jsonl")]
    paper = [
        "--strategies-dir",
        str(d),
        "--db",
        ":memory:",
        "--state",
        str(tmp_path / "paper.json"),
        "--kill-state",
        str(tmp_path / "kill.json"),
        "--heartbeat",
        str(tmp_path / "hb.json"),
    ]
    return base, paper, store, tmp_path


def approve(store):
    store.approve("siempre", 1, "camilo")


def audit_actions(tmp_path):
    return [json.loads(ln)["action"] for ln in (tmp_path / "audit.jsonl").read_text().splitlines()]


# --- paper ---------------------------------------------------------------------------------------


def test_paper_refuses_unapproved_strategies(env):
    base, paper, _, _ = env
    code, text = run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource())
    assert code == 2 and "ninguna versión aprobada" in text


def test_paper_requires_the_key(env, monkeypatch):
    base, paper, store, _ = env
    approve(store)
    monkeypatch.delenv("TRADEBOT_KEY")
    code, text = run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource())
    assert code == 2 and "TRADEBOT_KEY" in text


@pytest.mark.parametrize("mode", ["live", "real", "LIVE"])
def test_real_money_mode_is_not_available(env, monkeypatch, mode):
    base, paper, store, _ = env
    approve(store)
    monkeypatch.setenv("TRADING_MODE", mode)
    code, text = run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource())
    assert code == 2 and "no está implementado" in text


def test_paper_once_buys_and_audits(env):
    base, paper, store, tmp = env
    approve(store)
    code, text = run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource())
    assert code == 0 and text.startswith("buy:")
    assert {"bot.start", "order.check", "order.fill"} <= set(audit_actions(tmp))
    code, text = run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource())
    assert code == 0 and text.startswith("hold:")  # misma vela: no duplica
    assert run_cli([*base, "audit-verify"])[0] == 0


def test_paper_does_not_trade_on_stale_data(env):
    base, paper, store, tmp = env
    approve(store)
    code, text = run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource(hours_old=10))
    assert code == 0 and text.startswith("skip:") and "viejos" in text
    assert "order.fill" not in audit_actions(tmp)


def test_paper_reports_data_source_failures(env):
    base, paper, store, _ = env
    approve(store)
    code, text = run_cli([*base, "paper", "siempre", "--once", *paper], BrokenSource())
    assert code == 3 and "sin red" in text


def test_paper_refuses_a_state_with_another_symbol(env):
    base, paper, store, tmp = env
    approve(store)
    run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource())
    state = tmp / "paper.json"
    d = json.loads(state.read_text())
    d["position"]["symbol"] = "ETH/USDT"
    state.write_text(json.dumps(d))
    code, text = run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource())
    assert code == 2 and "distinta" in text


def test_paper_loop_mode_and_ctrl_c(env, monkeypatch):
    base, paper, store, tmp = env
    approve(store)
    monkeypatch.setattr(cli, "run_loop", lambda *a, **k: 0)
    code, text = run_cli([*base, "paper", "siempre", *paper], FreshSource())
    assert code == 0 and "Bot en marcha" in text

    def interrupted(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_loop", interrupted)
    code, text = run_cli([*base, "paper", "siempre", *paper], FreshSource())
    assert code == 0 and "Bot detenido" in text and "bot.stop" in audit_actions(tmp)


def test_editing_the_strategy_after_approval_blocks_start(env):
    base, paper, store, tmp = env
    approve(store)
    path = tmp / "s" / "siempre" / "v1.json"
    path.write_text(path.read_text().replace('"0.02"', '"0.5"'))
    code, text = run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource())
    assert code == 2 and "ninguna versión aprobada" in text


# --- status --------------------------------------------------------------------------------------


def test_status_with_nothing_running(env):
    _, paper, _, _ = env
    code, text = run_cli(["status", *paper[-6:]])
    assert code == 0 and "Latido: ninguno" in text and "desactivado" in text and "sin estado" in text


def test_status_after_a_run_shows_position_and_kill_switch(env):
    base, paper, store, _ = env
    approve(store)
    run_cli([*base, "paper", "siempre", "--once", *paper], FreshSource())
    run_cli([*base, "kill", "--state", paper[paper.index("--kill-state") + 1]])
    code, text = run_cli(["status", *paper[-6:]])
    assert code == 0 and "ACTIVADO" in text and "BTC/USDT" in text and "Órdenes ejecutadas: 1" in text


def test_status_flags_a_stalled_bot_and_bad_files(env):
    _, paper, _, tmp = env
    old = datetime.now(UTC) - timedelta(hours=3)
    hb = {
        "ts": old.isoformat(),
        "status": "ok",
        "detail": "hold: x",
        "failures": 0,
        "next_due": old.isoformat(),
    }
    (tmp / "hb.json").write_text(json.dumps(hb))
    assert "POSIBLEMENTE CAÍDO" in run_cli(["status", *paper[-6:]])[1]
    (tmp / "hb.json").write_text("no es json")
    (tmp / "paper.json").write_text("roto")
    code, text = run_cli(["status", *paper[-6:]])
    assert code == 3 and "Latido: ilegible" in text and "ilegible o incoherente" in text


def test_paper_once_writes_alerts_and_accepts_drawdown_limit(env):
    base, paper, store, tmp_path = env
    approve(store)
    alerts = ["--alerts", str(tmp_path / "alerts.log")]
    once = [*base, "paper", "siempre", "--once", *alerts]
    code, text = run_cli([*once, "--max-drawdown-pct", "0.05", *paper], FreshSource())
    assert code == 0 and text.startswith("buy:")
    assert "buy" in (tmp_path / "alerts.log").read_text(encoding="utf-8")
    assert run_cli([*once, "--max-drawdown-pct", "0", *paper], FreshSource())[0] == 2
