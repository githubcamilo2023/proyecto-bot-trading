import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from tradebot.cli import main
from tradebot.models import Candle

SPEC = {
    "name": "onda",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "size_pct": "0.02",
    "entry": {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": "<", "right": 30},
    "exit": {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": ">", "right": 55},
}


class WaveSource:
    """Velas horarias sintéticas (onda senoidal) hasta la hora actual."""

    def fetch(self, symbol, timeframe, since):
        t = since.replace(minute=0, second=0, microsecond=0)
        end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        out = []
        while t <= end:
            p = D(str(round(100 + 10 * math.sin(t.timestamp() / 3600 / 5), 4)))
            out.append(Candle(t, p, p, p, p, D(1)))
            t += timedelta(hours=1)
        return out


class BrokenSource:
    def fetch(self, symbol, timeframe, since):
        raise RuntimeError("sin red")


@pytest.fixture
def spec_file(tmp_path):
    path = tmp_path / "onda.json"
    path.write_text(json.dumps(SPEC), encoding="utf-8")
    return path


def run_cli(args, source=None):
    lines: list[str] = []
    code = main(args, source=source, out=lines.append)
    return code, "\n".join(lines)


def test_backtest_prints_a_summary(spec_file):
    code, text = run_cli(["backtest", str(spec_file), "--days", "10", "--db", ":memory:"], WaveSource())
    assert code == 0
    assert "Estrategia: onda v1 · BTC/USDT 1h" in text
    assert "Caída máxima" in text and "Comprar y mantener" in text


def test_backtest_runs_a_saved_strategy_by_name(tmp_path):
    from tradebot.strategy import Strategy, StrategyStore

    store_dir = tmp_path / "estrategias"
    StrategyStore(store_dir).save(Strategy.model_validate(SPEC))
    args = ["backtest", "onda", "--strategies-dir", str(store_dir), "--days", "5", "--db", ":memory:"]
    code, text = run_cli(args, WaveSource())
    assert code == 0 and "onda v1" in text


def test_risk_flags_are_passed_through(spec_file):
    args = ["backtest", str(spec_file), "--days", "10", "--db", ":memory:", "--max-order-pct", "0.01"]
    code, text = run_cli(args, WaveSource())
    assert code == 0 and "rechazadas por riesgo" in text  # size_pct 0.02 > límite 0.01


def test_unknown_strategy_is_a_clear_error(tmp_path):
    code, text = run_cli(["backtest", "no-existe", "--strategies-dir", str(tmp_path)])
    assert code == 2 and text.startswith("Error:")


def test_invalid_strategy_file_is_a_clear_error(tmp_path):
    bad = tmp_path / "mala.json"
    bad.write_text(json.dumps(SPEC | {"campo_inventado": 1}), encoding="utf-8")
    code, text = run_cli(["backtest", str(bad)])
    assert code == 2 and "campo_inventado" in text


def test_non_positive_days_is_rejected(spec_file):
    assert run_cli(["backtest", str(spec_file), "--days", "0"])[0] == 2


def test_data_source_failure_is_reported_without_traceback(spec_file):
    code, text = run_cli(["backtest", str(spec_file), "--db", ":memory:"], BrokenSource())
    assert code == 3 and "sin red" in text


def test_invalid_number_is_rejected_by_argparse(spec_file):
    with pytest.raises(SystemExit):
        main(["backtest", str(spec_file), "--equity", "mucho"])


def test_evaluate_reports_each_segment_and_honest_verdict(spec_file):
    code, text = run_cli(["evaluate", str(spec_file), "--days", "60", "--db", ":memory:"], WaveSource())
    assert code == 0
    assert "Evaluación por tramos: onda v1" in text
    assert "Tramo 1:" in text and "Tramo 4:" in text
    assert "Tramos con ganancia:" in text
    assert "garantiza" in text or "Muestra pequeña" in text or "No es consistente" in text


def test_evaluate_validates_arguments(spec_file):
    base = ["evaluate", str(spec_file), "--db", ":memory:"]
    assert run_cli([*base, "--segments", "1"], WaveSource())[0] == 2
    code, text = run_cli([*base, "--days", "3", "--segments", "4"], WaveSource())
    assert code == 2 and "muy poca historia" in text
    assert run_cli([*base, "--days", "60"], BrokenSource())[0] == 3


def test_backtest_summary_includes_profit_factor_when_there_are_losses(spec_file):
    code, text = run_cli(["backtest", str(spec_file), "--days", "60", "--db", ":memory:"], WaveSource())
    assert code == 0 and ("factor de beneficio" in text or "Salidas por stop-loss: 0" in text)


# --- comandos de control: approve / kill / release / audit-verify --------------------------------

KEY = "k" * 40


@pytest.fixture
def keyed(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEBOT_KEY", KEY)
    return ["--audit", str(tmp_path / "audit.jsonl")]


def _saved_store(tmp_path):
    from tradebot.strategy import Strategy, StrategyStore

    d = tmp_path / "e"
    StrategyStore(d).save(Strategy.model_validate(SPEC))
    return d


def test_approve_command_signs_audits_and_records_the_user(tmp_path, keyed):
    from tradebot.strategy import StrategyStore

    d = _saved_store(tmp_path)
    code, text = run_cli([*keyed, "approve", "onda", "1", "--user", "camilo", "--strategies-dir", str(d)])
    assert code == 0 and "camilo" in text
    assert StrategyStore(d, key=KEY.encode()).is_approved("onda", 1)
    code, text = run_cli([*keyed, "audit-verify"])
    assert code == 0 and "1 registros" in text


def test_approve_without_key_fails_cleanly(tmp_path):
    code, text = run_cli(["approve", "onda", "1", "--strategies-dir", str(tmp_path)])
    assert code == 2 and "TRADEBOT_KEY" in text


def test_approve_unknown_version_fails_cleanly(tmp_path, keyed):
    code, text = run_cli([*keyed, "approve", "onda", "9", "--strategies-dir", str(tmp_path)])
    assert code == 2 and text.startswith("Error:")


def test_kill_and_release_commands(tmp_path, keyed):
    from tradebot.models import RiskLimits
    from tradebot.risk import RiskEngine

    state = str(tmp_path / "kill.json")
    code, text = run_cli([*keyed, "kill", "--reason", "prueba", "--state", state])
    assert code == 0 and "ACTIVADO" in text
    assert RiskEngine(RiskLimits(), D(1), state).killed
    code, text = run_cli([*keyed, "release", "--state", state])
    assert code == 0 and "liberado" in text
    assert not RiskEngine(RiskLimits(), D(1), state).killed
    assert run_cli([*keyed, "audit-verify"])[1].startswith("Auditoría íntegra: 2 registros")


def test_kill_works_without_key_but_warns(tmp_path):
    state = str(tmp_path / "kill.json")
    code, text = run_cli(["kill", "--state", state])
    assert code == 0 and "SIN registro de auditoría" in text and "ACTIVADO" in text


def test_release_requires_the_key(tmp_path):
    code, text = run_cli(["release", "--state", str(tmp_path / "kill.json")])
    assert code == 2 and "TRADEBOT_KEY" in text


def test_kill_reports_when_it_cannot_persist(tmp_path):
    blocker = tmp_path / "bloqueado"
    blocker.write_text("x")
    code, text = run_cli(["kill", "--state", str(blocker / "kill.json")])
    assert code == 3 and "ATENCIÓN" in text


def test_release_reports_when_it_cannot_persist(tmp_path, keyed):
    blocker = tmp_path / "bloqueado"
    blocker.write_text("x")
    code, text = run_cli([*keyed, "release", "--state", str(blocker / "kill.json")])
    assert code == 2 and text.startswith("Error:")


def test_audit_verify_detects_tampering(tmp_path, keyed):
    d = _saved_store(tmp_path)
    run_cli([*keyed, "approve", "onda", "1", "--user", "camilo", "--strategies-dir", str(d)])
    audit = tmp_path / "audit.jsonl"
    audit.write_text(audit.read_text(encoding="utf-8").replace("camilo", "mallory"), encoding="utf-8")
    code, text = run_cli([*keyed, "audit-verify"])
    assert code == 4 and "ALTERADA" in text


def test_invalid_log_level_is_rejected():
    code, text = run_cli(["--log-level", "RUIDOSO", "kill"])
    assert code == 2 and "nivel de log" in text
