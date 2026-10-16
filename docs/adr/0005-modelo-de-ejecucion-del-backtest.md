# ADR 0005: Modelo de ejecución del backtest

- **Estado:** aceptado
- **Fecha:** 2026-10-15

## Contexto

Un backtest optimista es peor que ninguno: da confianza falsa. Hay que fijar reglas que eviten los sesgos más comunes y que el bot en vivo pueda replicar.

## Decisión

1. **Sin mirar el futuro**: la señal se calcula con velas **cerradas** hasta la vela *i* y la orden se ejecuta en la **apertura de la vela *i+1***.
2. **Costes realistas por defecto**: comisión 0,1 % y deslizamiento 0,05 % por operación (`config.py`), ajustables por línea de comandos.
3. **Misma ventana que en vivo**: la estrategia ve como máximo `max(600, 3 × velas mínimas)` velas, no todo el historial.
4. **Mismo motor de riesgo** que en vivo: cada compra pasa por él; las rechazadas se cuentan y se muestran.
5. **Pérdida diaria medida por día UTC**, desde el último cierre del día anterior.
6. **Cantidades** redondeadas hacia abajo a 8 decimales (constante provisional hasta leer las reglas de lote de cada exchange).
7. **Referencia**: se compara con comprar y mantener desde que la estrategia ya podía operar.
8. **Stop-loss intravela:** si el mínimo de la vela toca `entrada × (1 − stop_loss_pct)`, sale a ese precio; si la vela abre ya por debajo, sale a la apertura (hueco). Se evalúa también en la misma vela de la entrada, que es la hipótesis conservadora.
9. **Honestidad estadística**: con menos de 30 operaciones, el resumen avisa de que la muestra no prueba nada.

## Alternativas descartadas

- **Ejecutar al cierre de la misma vela de la señal**: introduce información que aún no existía.
- **Backtest sin costes**: sobrevalora estrategias que operan mucho.

## Consecuencias

- Los resultados salen más modestos y más creíbles.
- No modela: huecos de liquidez, rechazos del exchange, latencia ni ejecución parcial; un buen backtest **no** garantiza resultados futuros.
- No hay take-profit ni lectura de las reglas de lote reales del exchange (pendiente en `execution`).
