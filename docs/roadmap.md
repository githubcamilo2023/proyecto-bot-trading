# Hoja de ruta: lo mejor de freqtrade sin perder la intención del proyecto

**Intención que no se negocia.** Estrategias como DATOS validados (nunca código libre). La IA solo traduce y
propone; tú apruebas con firma. El motor de riesgo manda siempre. Todo queda en la auditoría. Paper por
defecto. Antes de añadir algo, se revisa si se puede simplificar.

De freqtrade (GPL-3.0) se toman **ideas**, no código. Si algún día se usa como ejecutor, será como programa
aparte.

## Fase 1: mejorar lo que ya corre en paper (sin exchange)
- [x] Protecciones en el motor de riesgo: espera tras salir y pausa tras varios stop-loss seguidos
- [x] Verificación de consistencia en `evaluate`: no usa datos futuros, no depende de cuánta historia ve
- [x] Prueba de estabilidad de parámetros (periodos ±20 %) en lugar de optimizar
- [x] Informe HTML de operaciones: gráfico con entradas y salidas, curva de capital, tabla y resumen mensual

## Fase 2: estrategias más expresivas
- [ ] Indicadores en lista cerrada: ATR, Bollinger, MACD, volumen
- [ ] Trailing stop y toma de ganancias (por porcentaje y por tiempo)
- [ ] Tamaño por riesgo en paper (decisión explícita del usuario sobre el tope por orden)

## Fase 3: realismo y operación
- [ ] Reglas reales del exchange (comisiones, lote y notional mínimos) leídas sin claves
- [ ] Telegram con `/estado` y `/frenar` (nunca aprobar ni editar desde fuera del PC)
- [ ] VPS 24/7 con Docker

## Fase 4: ejecución de prueba real
- [ ] Decidir: broker propio de Binance Demo o freqtrade como ejecutor desde el JSON aprobado
- [ ] Varios pares, cada uno con su estrategia aprobada y riesgo medido sobre el total

## Lo que NO se adopta (y por qué)
| De freqtrade | Motivo |
|---|---|
| Estrategias en Python libre | Rompe "reglas como datos": no se podrían validar ni aprobar con garantías |
| Hyperopt como optimizador | Fabrica sobreajuste; se reemplaza por la prueba de estabilidad |
| FreqAI (modelos que se re-entrenan) | Caja negra: no se puede aprobar algo que cambia solo |
| Futuros, apalancamiento y cortos | Multiplican el riesgo; fuera del MVP |
