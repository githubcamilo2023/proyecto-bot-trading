"""Informe HTML autocontenido de un backtest: cifras clave, precio con entradas/salidas, curva de capital,
resultado por mes y tabla de operaciones. Sin librerías ni internet: SVG generado aquí; abre en cualquier
navegador.
"""

from collections import OrderedDict
from datetime import datetime
from decimal import Decimal
from html import escape

from tradebot.backtest.engine import Result
from tradebot.models import Candle

W, H, PAD_L, PAD_R, PAD_T, PAD_B = 960, 300, 64, 16, 12, 28
MAX_POINTS = 1500  # una línea con más puntos no se ve mejor: se submuestrea (los marcadores no)

_CSS = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f2f1ee;--text-primary:#0b0b0b;
--text-secondary:#52514e;--grid:#e4e3df;--series-1:#2a78d6;--buy:#1baf7a;--sell:#eb6834}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
--surface-1:#1a1a19;--surface-2:#242423;--text-primary:#f0efec;--text-secondary:#c3c2b7;--grid:#34342f;
--series-1:#3987e5;--buy:#199e70;--sell:#d95926}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242423;
--text-primary:#f0efec;--text-secondary:#c3c2b7;--grid:#34342f;--series-1:#3987e5;--buy:#199e70;--sell:#d95926}
body{margin:0;background:var(--surface-1)}
.viz-root{background:var(--surface-1);color:var(--text-primary);font:14px/1.45 system-ui,sans-serif;
max-width:1000px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:24px 0 8px}
.muted{color:var(--text-secondary)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:16px 0}
.tile{background:var(--surface-2);border-radius:8px;padding:10px 12px}
.tile b{display:block;font-size:20px}.tile span{color:var(--text-secondary);font-size:12px}
svg{width:100%;height:auto;display:block}
svg text{fill:var(--text-secondary);font-size:11px}
.grid{stroke:var(--grid);stroke-width:1}
.line{fill:none;stroke:var(--series-1);stroke-width:2;stroke-linejoin:round}
.buy{fill:var(--buy);stroke:var(--surface-1);stroke-width:2}
.sell{fill:var(--sell);stroke:var(--surface-1);stroke-width:2}
.legend{display:flex;gap:16px;font-size:12px;color:var(--text-secondary);margin:4px 0}
.legend i{font-style:normal}.legend .b{color:var(--buy)}.legend .s{color:var(--sell)}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:right;padding:4px 8px;border-bottom:1px solid var(--grid)}
th:first-child,td:first-child{text-align:left}
.wrap{overflow-x:auto}
"""


def _fmt_pct(x: Decimal) -> str:
    return f"{x:+.2%}"


class _Scale:
    def __init__(self, t0: datetime, t1: datetime, lo: Decimal, hi: Decimal) -> None:
        pad = (hi - lo) * Decimal("0.05") or Decimal(1)
        self.t0, self.span = t0.timestamp(), max(t1.timestamp() - t0.timestamp(), 1.0)
        self.lo, self.hi = float(lo - pad), float(hi + pad)

    def x(self, t: datetime) -> float:
        return PAD_L + (t.timestamp() - self.t0) / self.span * (W - PAD_L - PAD_R)

    def y(self, v: Decimal) -> float:
        return PAD_T + (self.hi - float(v)) / (self.hi - self.lo) * (H - PAD_T - PAD_B)


def _axes(sc: _Scale, t0: datetime) -> list[str]:
    out = []
    for k in range(5):  # rejilla horizontal recesiva + etiquetas del eje y
        v = Decimal(str(sc.lo + (sc.hi - sc.lo) * (k + 0.5) / 5))
        y = sc.y(v)
        out.append(f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end">{v:,.0f}</text>')
    for k in range(5):  # fechas en el eje x
        t = datetime.fromtimestamp(sc.t0 + sc.span * k / 4, tz=t0.tzinfo)
        anchor = "start" if k == 0 else "end" if k == 4 else "middle"
        out.append(f'<text x="{sc.x(t):.1f}" y="{H - 8}" text-anchor="{anchor}">{t:%Y-%m-%d}</text>')
    return out


def _path(points: list[tuple[datetime, Decimal]], sc: _Scale) -> str:
    step = max(1, len(points) // MAX_POINTS)
    pts = points[::step] + ([points[-1]] if (len(points) - 1) % step else [])
    return "M" + " L".join(f"{sc.x(t):.1f},{sc.y(v):.1f}" for t, v in pts)


def _chart(points: list[tuple[datetime, Decimal]], label: str, marks: list[str] | None = None) -> str:
    t0, t1 = points[0][0], points[-1][0]
    values = [v for _, v in points]
    sc = _Scale(t0, t1, min(values), max(values))
    body = [
        *_axes(sc, t0),
        f'<path class="line" d="{_path(points, sc)}"><title>{escape(label)}</title></path>',
    ]
    return (
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}">'
        + "".join(body)
        + "".join(marks or [])
        + "</svg>"
    )


def _trade_marks(result: Result, candles: list[Candle]) -> list[str]:
    closes = [(c.ts, c.close) for c in candles]
    values = [v for _, v in closes]
    sc = _Scale(closes[0][0], closes[-1][0], min(values), max(values))
    out = []
    for t in result.trades:
        x, y = sc.x(t.entry_ts), sc.y(t.entry_price)
        out.append(
            f'<path class="buy" d="M{x:.1f},{y - 7:.1f} L{x + 6:.1f},{y + 4:.1f} L{x - 6:.1f},{y + 4:.1f} Z">'
            f"<title>COMPRA {t.entry_ts:%Y-%m-%d %H:%M} a {t.entry_price:,.2f}</title></path>"
        )
        x, y = sc.x(t.exit_ts), sc.y(t.exit_price)
        why = "stop-loss" if t.stopped else "regla de salida"
        out.append(
            f'<path class="sell" d="M{x:.1f},{y + 7:.1f} L{x + 6:.1f},{y - 4:.1f} '
            f'L{x - 6:.1f},{y - 4:.1f} Z">'
            f"<title>VENTA ({why}) {t.exit_ts:%Y-%m-%d %H:%M} a {t.exit_price:,.2f} · resultado "
            f"{t.pnl:+,.2f}</title></path>"
        )
    return out


def _monthly(result: Result) -> list[tuple[str, Decimal, Decimal]]:
    """(mes, capital al cierre, variación del mes)."""
    last: OrderedDict[str, Decimal] = OrderedDict()
    for t, e in result.curve:
        last[f"{t:%Y-%m}"] = e
    out, prev = [], result.initial_equity
    for month, eq in last.items():
        out.append((month, eq, eq / prev - 1))
        prev = eq
    return out


def render_html(result: Result, candles: list[Candle], note: str = "") -> str:
    r = result
    pf = "—" if r.profit_factor is None else f"{r.profit_factor:.2f}"
    wr = "—" if r.win_rate is None else f"{r.win_rate:.0%}"
    tiles = [
        ("Retorno", _fmt_pct(r.total_return)),
        ("Comprar y mantener", _fmt_pct(r.buy_hold_return)),
        ("Caída máxima", f"{r.max_drawdown:.2%}"),
        ("Operaciones", str(len(r.trades))),
        ("Aciertos", wr),
        ("Factor de beneficio", pf),
    ]
    rows = "".join(
        f"<tr><td>{t.entry_ts:%Y-%m-%d %H:%M}</td><td>{t.exit_ts:%Y-%m-%d %H:%M}</td>"
        f"<td>{t.entry_price:,.2f}</td><td>{t.exit_price:,.2f}</td><td>{t.pnl:+,.2f}</td>"
        f"<td>{'stop-loss' if t.stopped else 'regla'}</td></tr>"
        for t in r.trades
    )
    months = "".join(
        f"<tr><td>{m}</td><td>{eq:,.2f}</td><td>{_fmt_pct(chg)}</td></tr>" for m, eq, chg in _monthly(r)
    )
    price = [(c.ts, c.close) for c in candles]
    small = (
        f'<p class="muted">Muestra pequeña ({len(r.trades)} operaciones): con menos de 30 no se puede '
        "concluir nada.</p>"
        if len(r.trades) < 30
        else ""
    )
    blocked = ""
    if r.rejected:
        items = "".join(f"<li>{n} × {escape(why)}</li>" for why, n in r.rejected.items())
        kill = (
            "<p>El kill switch saltó por la caída máxima de capital: desde ahí no volvió a comprar.</p>"
            if "kill switch activo" in r.rejected
            else ""
        )
        title = "<span>Compras frenadas por el motor de riesgo</span>"
        blocked = f'<div class="tile">{title}<ul>{items}</ul>{kill}</div>'
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Informe {escape(r.label)}</title>
<style>{_CSS}</style></head><body><div class="viz-root">
<h1>{escape(r.label)}</h1>
<p class="muted">{r.curve[0][0]:%Y-%m-%d} → {r.curve[-1][0]:%Y-%m-%d} · simulación (backtest) con comisiones y
deslizamiento · {escape(note)} No es asesoría financiera.</p>
{small}
<div class="tiles">{"".join(f"<div class='tile'><b>{v}</b><span>{k}</span></div>" for k, v in tiles)}</div>
{blocked}
<h2>Precio con entradas y salidas</h2>
<div class="legend"><i class="b">▲ compra</i><i class="s">▼ venta</i>
<span>Pasa el mouse sobre un triángulo para ver el detalle.</span></div>
{_chart(price, "Precio de cierre", _trade_marks(r, candles))}
<h2>Capital</h2>
{_chart(list(r.curve), "Capital")}
<h2>Resultado por mes</h2>
<div class="wrap"><table><tr><th>Mes</th><th>Capital al cierre</th><th>Variación</th></tr>
{months}</table></div>
<h2>Operaciones</h2>
<div class="wrap"><table><tr><th>Entrada</th><th>Salida</th><th>Precio entrada</th><th>Precio salida</th>
<th>Resultado</th><th>Motivo</th></tr>
{rows or "<tr><td colspan='6'>Sin operaciones cerradas</td></tr>"}</table></div>
</div></body></html>
"""
