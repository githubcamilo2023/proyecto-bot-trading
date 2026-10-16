"""Constantes del proyecto en un solo lugar: nada de números mágicos repartidos por el código."""

from decimal import Decimal

DEFAULT_EQUITY = Decimal(10000)  # capital inicial por defecto de un backtest
FEE_PCT = Decimal("0.001")  # comisión por operación (0.1 %)
SLIPPAGE_PCT = Decimal("0.0005")  # deslizamiento por operación (0.05 %)
QTY_STEP = Decimal("0.00000001")  # precisión de cantidades (8 decimales) hasta leerla del exchange
MIN_WINDOW = 600  # velas que ve la estrategia en cada decisión (backtest y vivo deben coincidir)
MAX_RULE_DEPTH = 6  # niveles máximos de anidación all/any en una regla
MAX_RULE_CONDITIONS = 50  # comparaciones máximas en una regla
DEFAULT_STOP_LOSS_PCT = Decimal("0.03")  # pérdida máxima por operación si la estrategia no dice otra (3 %)
MAX_CANDLE_JUMP = Decimal("0.5")  # salto de cierre a cierre que se considera dato corrupto (50 %)
MAX_STALE_CANDLES = 1  # cuántas velas de retraso tolera la última vela antes de dejar de operar
MAX_TICK_FAILURES = 5  # fallos seguidos del bot antes de activar el kill switch y detenerse
