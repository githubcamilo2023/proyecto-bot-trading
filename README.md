# tradebot

Bot de trading al que se le dan directrices de estrategia por chat/panel web. Las directrices se convierten en reglas versionadas que ejecuta un motor determinista, siempre detrás de un motor de riesgo con límites duros. Modo por defecto: **paper trading**.

> Un bot puede perder capital. Este proyecto es una herramienta técnica, no asesoría financiera.

## Arranque rápido

```bash
python -m venv .venv
source .venv/bin/activate       # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -c constraints.txt -e ".[dev,llm]"     # versiones fijadas ([llm] = SDK de Claude)
pytest
tradebot backtest examples/rsi-sobrevendido.json --days 365
```

Para aprobar estrategias y operar con auditoría hace falta la clave de integridad: ver [`docs/runbook.md`](docs/runbook.md), sección 0. El backtest no la necesita.

> Si el proyecto vive en una carpeta sincronizada (OneDrive), crea el `.venv` y guarda el `.env` con claves de API fuera de ella: el entorno virtual sincroniza miles de archivos y el `.env` subiría tus claves a la nube.

## Estructura

```
src/tradebot/
  config.py        # constantes (comisión, ventana, topes de reglas)
  models.py        # Order, Candle, RiskLimits (dominio puro)
  ports.py         # Broker y MarketData (interfaces)
  timeframe.py     # '4h' -> segundos
  integrity.py     # hash y firma HMAC (clave TRADEBOT_KEY)
  audit.py         # auditoría permanente encadenada y firmada
  logging_setup.py # logs JSON con trace_id
  guard_secrets.py # guardia anti-secretos para pre-commit
  risk/engine.py   # límites duros + kill switch persistente
  data/            # velas públicas (CCXT) + caché local SQLite, solo velas cerradas
  strategy/        # reglas JSON validadas, indicadores, motor de señales, almacén versionado y aprobado
  backtest/        # simulación vela a vela con comisiones, deslizamiento y motor de riesgo
  llm/             # directriz en lenguaje natural -> estrategia validada (sin aprobar)
  execution/       # broker paper persistente, ciclo del bot, bucle con latido y freno automático
  cli.py           # backtest, propose, show, approve, paper, status, kill, release, audit-verify
examples/          # estrategias de ejemplo (JSON)
tests/
docs/              # modelo de amenazas, runbook, lista para pasar a real
docs/adr/          # decisiones de arquitectura
```

## Formato de estrategias

Una estrategia es un JSON estricto (campos desconocidos se rechazan). Indicadores disponibles: `close`, `sma`, `ema`, `rsi`; operadores: `<`, `<=`, `>`, `>=`, `crosses_above`, `crosses_below`; combinadores: `all` y `any`.

```json
{
  "name": "rsi-sobrevendido",
  "symbol": "BTC/USDT",
  "timeframe": "4h",
  "size_pct": "0.02",
  "entry": {"type": "all", "of": [
    {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": "<", "right": 30},
    {"type": "cmp", "left": {"kind": "close"}, "op": ">", "right": {"kind": "sma", "period": 200}}
  ]},
  "exit": {"type": "cmp", "left": {"kind": "rsi", "period": 14}, "op": ">", "right": 55}
}
```

`stop_loss_pct` (por defecto `"0.03"`, es decir 3 %) cierra la posición si el precio cae ese porcentaje desde la entrada. En el backtest se evalúa con el mínimo de cada vela y, si el precio abre por debajo del stop (hueco), sale a la apertura. Tras un stop, si la regla de entrada sigue cumpliéndose, la estrategia puede volver a entrar en la vela siguiente.

Cada guardado crea una versión nueva (`strategies/<nombre>/v1.json`, `v2.json`...) que no se sobrescribe. Para ejecutarla hay que aprobarla (`tradebot approve <nombre> <versión>`): la aprobación es un registro firmado ligado al contenido exacto, así que editar el archivo después la invalida. Solo se ejecuta lo aprobado. Detalles en [ADR 0002](docs/adr/0002-reglas-json-y-aprobacion-firmada.md).

## Dar directrices en lenguaje natural

```powershell
$env:TRADEBOT_KEY = "TU_CLAVE"        # 32+ caracteres, ver docs/runbook.md
$env:ANTHROPIC_API_KEY = "TU_CLAVE_API"  # clave de Anthropic, fuera de OneDrive
tradebot propose "compra cuando el RSI de 14 baje de 30 y vende cuando suba de 55, en BTC/USDT a 4 horas"
tradebot show rebote-rsi                # descripción en español generada por código, no por el LLM
tradebot backtest rebote-rsi --days 365
tradebot approve rebote-rsi 1          # solo tú; sin esto el bot no la ejecuta
```

En los ejemplos, `rebote-rsi` es el nombre de la estrategia y `1` su versión: cámbialos por los tuyos (los muestra `propose` al guardar). No escribas los símbolos `<` `>`: en PowerShell están reservados.

El LLM solo propone: su salida debe cumplir el esquema estricto y queda guardada **sin aprobar**. No ejecuta órdenes ni ve claves. Detalles en [ADR 0006](docs/adr/0006-llm-propone-humano-aprueba.md).

## Paper trading

```powershell
tradebot paper rebote-rsi --once   # un ciclo de prueba (la estrategia debe estar aprobada)
tradebot paper rebote-rsi          # continuo, un ciclo por vela
tradebot status                    # ¿está vivo? kill switch, efectivo, posición
```

Opera con dinero **simulado** y datos reales, con las mismas reglas de riesgo, stop-loss y auditoría. Es idempotente (reiniciar no duplica órdenes), no opera con datos viejos o con huecos, y se detiene solo (con el kill switch activado) tras 5 fallos seguidos. El modo real no existe todavía. Ver [ADR 0007](docs/adr/0007-ejecucion-paper-idempotente.md).

Módulo previsto (siguiente paso): `api` (panel web).

## Control y emergencias

```bash
tradebot approve rebote-rsi 1          # aprobar (exige TRADEBOT_KEY; queda en la auditoría)
tradebot kill --reason "motivo"        # EMERGENCIA: bloquea compras nuevas, también en un bot en marcha
tradebot release                       # reanudar (exige clave; queda en la auditoría)
tradebot audit-verify                  # comprueba que el historial no fue alterado
```

Procedimientos completos en [`docs/runbook.md`](docs/runbook.md); amenazas y límites en [`docs/threat-model.md`](docs/threat-model.md); requisitos para dinero real en [`docs/go-live-checklist.md`](docs/go-live-checklist.md).

## Desarrollo

```bash
ruff check . && ruff format --check .   # estilo
mypy                                    # tipos estrictos
pytest --cov=tradebot                   # tests (CI exige >= 90 %)
pip-audit                               # dependencias con vulnerabilidades conocidas
pre-commit install && pre-commit install --hook-type pre-push   # opcional: lo mismo antes de cada commit
```

CI (`.github/workflows/ci.yml`) ejecuta todo esto en Linux (Python 3.11–3.13) y Windows.

## Probar una estrategia (backtest)

```bash
tradebot backtest examples/rsi-sobrevendido.json --days 365
# equivalente sin instalar el comando: python -m tradebot backtest examples/rsi-sobrevendido.json
```

Descarga velas públicas (necesita internet; si Binance está bloqueado en tu región, añade `--exchange kraken`), las guarda en `data/candles.db` y simula la estrategia. Imprime retorno, comparación contra comprar y mantener, caída máxima, operaciones, comisiones y órdenes rechazadas por riesgo.

Opciones: `--days`, `--equity`, `--fee`, `--slippage`, `--max-order-pct`, `--max-daily-loss-pct`, `--exchange`, `--db`.

Cómo simula: la señal sale del **cierre** de una vela y la orden se ejecuta en la **apertura de la siguiente** (sin mirar el futuro). Es spot, con una posición a la vez, y cada compra pasa por el motor de riesgo. Por defecto el riesgo permite como máximo el 2% del capital por orden, así que los retornos salen pequeños a propósito; para más exposición hay que subir `--max-order-pct` y el `size_pct` de la estrategia. Con menos de 30 operaciones el resultado no es concluyente, y un buen backtest no garantiza resultados futuros.

## Plan de un mes

1. Estructura, datos y backtest.
2. Reglas de estrategia y riesgo.
3. Lenguaje natural → reglas y panel web.
4. Paper trading 24/7 (VPS) y auditoría.

Decisiones de fondo en `docs/adr/` (0001 arquitectura, 0002 reglas y aprobación, 0003 riesgo, 0004 persistencia, 0005 backtest, 0006 LLM, 0007 ejecución).
