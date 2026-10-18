from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from tradebot.backtest import run
from tradebot.backtest.report import _monthly, render_html
from tradebot.cli import main
from tradebot.models import Candle, RiskLimits
from tradebot.strategy import Strategy

T0 = datetime(2026, 1, 1, tzinfo=UTC)
SPEC = {
    "name": "t", "symbol": "BTC/USDT", "timeframe": "1d", "size_pct": "0.5", "stop_loss_pct": "0.5",
    "entry": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 0},
    "exit": {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": 105},
}  # fmt: skip


def candles(rows):
    return [
        Candle(T0 + timedelta(days=i), D(o), D(max(o, c)), D(min(o, c)), D(c), D(1))
        for i, (o, c) in enumerate(rows)
    ]


ROWS = [(100, 100), (100, 110), (100, 100), (100, 100), (100, 110), (100, 100)] * 10


def result():
    return run(
        Strategy.model_validate(SPEC), candles(ROWS), RiskLimits(max_order_pct=D(1)), D(1000), D(0), D(0)
    )


def test_report_is_self_contained_and_has_every_section():
    html = render_html(result(), candles(ROWS), "nota <script>")
    for part in (
        "Precio con entradas y salidas",
        "Capital",
        "Resultado por mes",
        "Operaciones",
        "Factor de beneficio",
    ):
        assert part in html
    assert 'class="buy"' in html and 'class="sell"' in html  # marcadores por forma y color
    assert "<script" not in html.replace("&lt;script", "")  # texto escapado, sin scripts
    assert "http://" not in html and "https://" not in html  # sin recursos externos
    assert "prefers-color-scheme:dark" in html and 'data-theme="dark"' in html
    assert "Muestra pequeña" in html and "No es asesoría financiera" in html


def test_report_explains_why_buys_were_blocked():
    html = render_html(result(), candles(ROWS))
    assert "Compras frenadas por el motor de riesgo" in html and "espera tras salir" in html


def test_monthly_changes_chain_from_the_initial_equity():
    months = _monthly(result())
    assert months[0][0] == "2026-01" and months[-1][0] == "2026-03"
    total = D(1)
    for _, _, chg in months:
        total *= 1 + chg
    assert abs(total - result().final_equity / 1000) < D("0.000001")


def test_report_command_writes_the_file(tmp_path, monkeypatch):
    import json

    class Src:
        def fetch(self, symbol, timeframe, since):
            end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
            n = (end - since.replace(hour=0, minute=0, second=0, microsecond=0)).days
            rows = (ROWS * 50)[: n + 1]
            start = end - timedelta(days=len(rows) - 1)
            return [
                Candle(start + timedelta(days=i), D(o), D(max(o, c)), D(min(o, c)), D(c), D(1))
                for i, (o, c) in enumerate(rows)
            ]

    f = tmp_path / "t.json"
    f.write_text(json.dumps(SPEC), encoding="utf-8")
    lines: list[str] = []
    out_dir = tmp_path / "rep"
    code = main(
        ["report", str(f), "--days", "60", "--db", ":memory:", "--out", str(out_dir)],
        source=Src(),
        out=lines.append,
    )
    assert code == 0 and (out_dir / "t-v1.html").exists()
    assert any("Informe con gráfico:" in ln for ln in lines)
    blocker = tmp_path / "archivo"
    blocker.write_text("x")
    bad = ["report", str(f), "--days", "60", "--db", ":memory:", "--out", str(blocker / "sub")]
    assert main(bad, source=Src(), out=lines.append) == 2
