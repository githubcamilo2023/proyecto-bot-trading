import json

import pytest

from tradebot.cli import main
from tradebot.strategy import StrategyStore, templates
from tradebot.strategy.review import origin_of

KEY = "k" * 40


def run(args, llm=None):
    lines: list[str] = []
    code = main(args, out=lines.append, llm=llm)
    return code, "\n".join(lines)


def test_every_template_is_valid_documented_and_named_like_its_file():
    names = templates.names()
    assert len(names) >= 6
    for name in names:
        s = templates.load(name)
        assert s.name == name
        assert s.notes.startswith("Familia:") and "Fuente:" in s.notes and "Qué esperar:" in s.notes
        assert s.size_pct == templates.load(name).size_pct <= 0.02  # conservadoras por defecto


def test_unknown_template_lists_the_available_ones():
    with pytest.raises(FileNotFoundError, match="disponibles: "):
        templates.load("no-existe")


def test_prompt_catalog_is_compact_and_has_every_template():
    text = templates.for_prompt()
    assert all(n in text for n in templates.names()) and "Fuente" not in text
    assert len(text) < 4000


def test_templates_command_shows_family_source_and_expectations():
    code, text = run(["templates"])
    assert code == 0 and text.count("Fuente:") == len(templates.names()) and "tradebot import" in text


def test_import_template_saves_unapproved_version_and_records_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEBOT_KEY", KEY)
    audit = tmp_path / "audit.jsonl"
    d = tmp_path / "s"
    code, text = run(["--audit", str(audit), "import", "sma-filtro-tendencia", "--strategies-dir", str(d)])
    assert code == 0 and "Guardada como sma-filtro-tendencia v1, SIN APROBAR" in text
    store = StrategyStore(d, key=KEY.encode())
    assert store.load("sma-filtro-tendencia").timeframe == "1d"
    with pytest.raises(Exception, match="ninguna versión aprobada"):
        store.load_approved("sma-filtro-tendencia")  # importar NUNCA aprueba
    records = [json.loads(ln) for ln in audit.read_text().splitlines()]
    assert any(r["action"] == "strategy.import" for r in records)
    assert "plantilla sma-filtro-tendencia" in (origin_of(audit, "sma-filtro-tendencia", 1) or "")
    # importar otra vez crea v2, no pisa la v1
    run(["--audit", str(audit), "import", "sma-filtro-tendencia", "--strategies-dir", str(d)])
    assert store.versions("sma-filtro-tendencia") == [1, 2]


def test_import_from_a_json_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEBOT_KEY", KEY)
    f = tmp_path / "mia.json"
    f.write_text(templates.load("ema-cruce-tendencia").model_dump_json(exclude={"version"}), encoding="utf-8")
    code, text = run(
        ["--audit", str(tmp_path / "a.jsonl"), "import", str(f), "--strategies-dir", str(tmp_path)]
    )
    assert code == 0 and "archivo mia.json" in text


def test_import_errors(tmp_path, monkeypatch):
    base = ["--audit", str(tmp_path / "a.jsonl"), "import"]
    assert run([*base, "sma-filtro-tendencia"])[0] == 2  # sin clave
    monkeypatch.setenv("TRADEBOT_KEY", KEY)
    assert run([*base, "no-existe", "--strategies-dir", str(tmp_path)])[0] == 2
    bad = tmp_path / "mala.json"
    bad.write_text('{"name": "x"}', encoding="utf-8")
    assert run([*base, str(bad), "--strategies-dir", str(tmp_path)])[0] == 2


def test_evaluate_accepts_a_template_name_and_prefers_a_saved_strategy(tmp_path):
    from tradebot.cli import _load

    assert _load("rsi2-connors-diario", str(tmp_path)).name == "rsi2-connors-diario"
    store = StrategyStore(tmp_path)
    custom = templates.load("rsi2-connors-diario").model_copy(update={"notes": "mía"})
    store.save(custom)
    assert _load("rsi2-connors-diario", str(tmp_path)).notes == "mía"
    with pytest.raises(FileNotFoundError):
        _load("ni-guardada-ni-plantilla", str(tmp_path))


def test_the_model_receives_the_library_as_context():
    from tradebot.llm import propose

    class Spy:
        system = ""

        def complete(self, system, user):
            Spy.system = system
            return json.dumps(
                templates.load("sma-filtro-tendencia").model_dump(mode="json", exclude={"version"})
            )

    propose("sigue la tendencia diaria de BTC", Spy())
    assert "familias documentadas" in Spy.system and "tendencia-sma200-diaria" in Spy.system


# --- tamaño por riesgo ------------------------------------------------------------------------------

from decimal import Decimal as D  # noqa: E402

from tradebot.strategy import Strategy  # noqa: E402
from tradebot.strategy.describe import describe  # noqa: E402


def with_risk(risk, size="0.5", stop="0.05"):
    data = templates.load("sma-filtro-tendencia").model_dump(mode="json")
    return Strategy.model_validate(data | {"risk_pct": risk, "size_pct": size, "stop_loss_pct": stop})


def test_order_fraction_sizes_by_risk_and_respects_the_cap():
    assert with_risk("0.01").order_fraction == D("0.2")  # 1 % / 5 % = 20 % del capital
    assert with_risk("0.05", stop="0.05").order_fraction == D("0.5")  # 100 % pedido -> tope 50 %
    assert templates.load("sma-filtro-tendencia").order_fraction == D("0.02")  # sin risk_pct: igual que antes


@pytest.mark.parametrize("bad", ["0", "0.06", "-0.01"])
def test_risk_pct_is_limited_to_five_percent(bad):
    with pytest.raises(ValueError):
        with_risk(bad)


def test_describe_explains_risk_sizing_in_plain_words():
    text = describe(with_risk("0.01"))
    assert "arriesga 1" in text and "posición de 20.00% del capital" in text


def test_backtest_uses_the_risk_based_position():
    from datetime import UTC, datetime, timedelta

    from tradebot.backtest import run
    from tradebot.models import Candle, RiskLimits

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [100] * 60 + [120] * 3  # la entrada cruza sobre la SMA 50 al subir
    candles = [Candle(t0 + timedelta(days=i), D(p), D(p), D(p), D(p), D(1)) for i, p in enumerate(rows)]
    limits = RiskLimits(max_order_pct=D("0.5"))
    fixed = run(templates.load("sma-filtro-tendencia"), candles, limits, D(10000), D(0), D(0))
    risky = run(with_risk("0.01"), candles, limits, D(10000), D(0), D(0))
    assert fixed.open_at_end and risky.open_at_end
    gain_fixed, gain_risky = fixed.final_equity - 10000, risky.final_equity - 10000
    assert gain_risky == gain_fixed * 10  # 20 % invertido contra 2 %


def test_evaluate_with_risk_pct_is_a_clearly_labeled_simulation(tmp_path):
    import math
    from datetime import UTC, datetime, timedelta

    from tradebot.models import Candle

    class Wave:
        def fetch(self, symbol, timeframe, since):
            t = since.replace(hour=0, minute=0, second=0, microsecond=0)
            end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
            out = []
            while t <= end:
                p = D(str(round(100 + 20 * math.sin(t.timestamp() / 86400 / 30), 4)))
                out.append(Candle(t, p, p, p, p, D(1)))
                t += timedelta(days=1)
            return out

    lines: list[str] = []
    args = ["evaluate", "sma-filtro-tendencia", "--days", "1460", "--db", ":memory:", "--risk-pct", "0.01"]
    code = main(args, source=Wave(), out=lines.append)
    text = "\n".join(lines)
    assert code == 0 and "SIMULACIÓN con tamaño por riesgo" in text and "posición de 20.00%" in text
    bad = ["evaluate", "sma-filtro-tendencia", "--db", ":memory:", "--risk-pct", "0.2"]
    assert main(bad, source=Wave(), out=lines.append) == 2
