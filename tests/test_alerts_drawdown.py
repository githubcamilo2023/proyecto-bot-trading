import json
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from tradebot.alerts import CHAT_ENV, TOKEN_ENV, make_notifier
from tradebot.models import RiskLimits
from tradebot.risk import RiskEngine

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


# --- alertas ------------------------------------------------------------------------------------------


def test_alert_always_goes_to_the_local_file(tmp_path):
    n = make_notifier(tmp_path / "x" / "alerts.log", env={}, now=lambda: NOW)
    n("compra  hecha\ncon salto")
    assert (tmp_path / "x" / "alerts.log").read_text(encoding="utf-8") == (
        "2026-10-18 12:00:00 UTC  compra hecha con salto\n"
    )


def test_telegram_is_used_only_when_both_variables_exist(tmp_path):
    sent = []
    make_notifier(tmp_path / "a.log", {TOKEN_ENV: "t"}, lambda *a: sent.append(a))("hola")
    assert sent == []
    make_notifier(tmp_path / "a.log", {TOKEN_ENV: "t", CHAT_ENV: "c"}, lambda *a: sent.append(a))("hola")
    assert sent == [("t", "c", "TRADEBOT: hola")]


def test_failed_telegram_never_raises_nor_leaks_the_token(tmp_path, caplog):
    def boom(token, chat, text):
        raise RuntimeError(f"https://api.telegram.org/bot{token}/sendMessage falló")

    n = make_notifier(tmp_path / "a.log", {TOKEN_ENV: "SECRETO123", CHAT_ENV: "c"}, boom)
    with caplog.at_level("ERROR"):
        n("hola")
    assert "SECRETO123" not in caplog.text and "RuntimeError" in caplog.text
    assert "hola" in (tmp_path / "a.log").read_text(encoding="utf-8")  # el archivo se escribió igual


def test_unwritable_alert_file_does_not_raise(tmp_path):
    blocker = tmp_path / "bloqueado"
    blocker.write_text("soy un archivo, no una carpeta")
    make_notifier(blocker / "a.log", env={})("hola")  # no lanza


def test_message_is_truncated(tmp_path):
    n = make_notifier(tmp_path / "a.log", env={}, now=lambda: NOW)
    n("x" * 5000)
    assert len((tmp_path / "a.log").read_text(encoding="utf-8")) < 1100


# --- caída máxima ---------------------------------------------------------------------------------------


def engine(tmp_path, dd="0.10"):
    return RiskEngine(RiskLimits(max_drawdown_pct=D(dd)), D(10000), tmp_path / "kill.json")


def test_drawdown_trips_the_kill_switch_but_keeps_sells_allowed(tmp_path):
    r = engine(tmp_path)
    assert r.observe(D(10000)) is False
    assert r.observe(D(9100)) is False  # -9 %: aún dentro del límite
    assert r.observe(D(8999)) is True and r.killed
    assert r.observe(D(8000)) is False  # ya estaba activado: no repite
    from tradebot.models import Order, Side

    sell = Order("BTC/USDT", Side.SELL, D(1), D(100), NOW)
    buy = Order("BTC/USDT", Side.BUY, D(1), D(100), NOW)
    assert r.check(sell, 1, D(0)).allowed and not r.check(buy, 0, D(0)).allowed


def test_peak_follows_new_highs_and_survives_restart(tmp_path):
    r = engine(tmp_path)
    r.observe(D(10000))
    r.observe(D(12000))  # nuevo máximo
    r2 = engine(tmp_path)  # reinicio: el máximo se lee del disco
    assert r2.observe(D(11000)) is False  # -8.3 % desde 12000
    assert r2.observe(D(10800)) is True  # -10 % exacto desde 12000


def test_release_resets_the_high_water_mark(tmp_path):
    r = engine(tmp_path)
    r.observe(D(10000))
    r.observe(D(8000))
    assert r.killed
    r.release("camilo")
    assert not r.killed and not (tmp_path / "kill.peak").exists()
    assert r.observe(D(8000)) is False  # nuevo máximo = capital actual: no vuelve a saltar


def test_corrupt_peak_file_resets_instead_of_crashing(tmp_path):
    r = engine(tmp_path)
    r.observe(D(10000))
    (tmp_path / "kill.peak").write_text("basura")
    assert r.observe(D(9500)) is False


def test_engine_without_state_file_keeps_peak_in_memory():
    r = RiskEngine(RiskLimits(), D(10000))
    r.observe(D(10000))
    assert r.observe(D(8900)) is True and r.killed


def test_drawdown_limit_is_validated():
    with pytest.raises(ValueError):
        RiskLimits(max_drawdown_pct=D(0))
    with pytest.raises(ValueError):
        RiskLimits(max_drawdown_pct=D("1.5"))


def test_kill_state_file_is_not_polluted_by_the_peak(tmp_path):
    r = engine(tmp_path)
    r.observe(D(10000))
    r.kill("manual", "camilo")
    assert set(json.loads((tmp_path / "kill.json").read_text())) == {"killed", "reason", "actor", "at"}


def test_release_from_another_console_reaches_a_running_bot_after_drawdown(tmp_path):
    running = engine(tmp_path)  # el motor dentro del bot en marcha
    running.observe(D(10000))
    assert running.observe(D(8000)) is True and running.killed
    engine(tmp_path).release("camilo")  # `tradebot release` en otra consola
    assert not running.killed  # el bot en marcha debe enterarse sin reiniciar
    assert running.observe(D(8000)) is False  # y no vuelve a saltar: el máximo se reinició


def test_kill_reason_is_readable_for_the_panel(tmp_path):
    r = engine(tmp_path)
    assert r.kill_reason() == ""
    r.observe(D(10000))
    r.observe(D(8000))
    assert "caída máxima del 10%" in engine(tmp_path).kill_reason()
    (tmp_path / "kill.json").write_text("{roto")
    assert engine(tmp_path).kill_reason() == "estado ilegible"
    assert RiskEngine(RiskLimits(), D(1)).kill_reason() == ""


def test_panel_and_status_show_the_kill_reason(tmp_path):
    from tradebot.cli import main

    r = engine(tmp_path)
    r.observe(D(10000))
    r.observe(D(8000))
    lines: list[str] = []
    main(["status", "--kill-state", str(tmp_path / "kill.json"), "--state", str(tmp_path / "p.json"),
          "--heartbeat", str(tmp_path / "hb.json")], out=lines.append)  # fmt: skip
    assert any("motivo: caída máxima" in ln for ln in lines)
    from tradebot.watch import _killed

    assert "caída máxima" in str(_killed(tmp_path / "kill.json"))
    assert _killed(tmp_path / "nada.json") is False


def test_peak_and_kill_state_never_share_a_temp_file(tmp_path, monkeypatch):
    import os

    from tradebot.risk import engine as eng

    used: list[str] = []
    real = os.replace

    def spy(src, dst):
        used.append(os.path.basename(src))
        real(src, dst)

    monkeypatch.setattr(eng.os, "replace", spy)
    r = engine(tmp_path)
    r.observe(D(10000))
    r.kill("x", "camilo")
    assert len(set(used)) == 2 and all(str(os.getpid()) in u for u in used)
    assert not list(tmp_path.glob("*.tmp"))  # no quedan temporales
