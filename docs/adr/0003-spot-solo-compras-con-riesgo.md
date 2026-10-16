# ADR 0003: Spot, una posición, compras filtradas por riesgo y ventas siempre permitidas

- **Estado:** aceptado
- **Fecha:** 2026-10-15

## Contexto

El MVP opera cripto spot. Hay que decidir qué hace el motor de riesgo con cada orden y cuándo frena, sabiendo que un freno que bloquee salidas puede empeorar una pérdida.

## Decisión

1. **Spot y solo largo**: sin apalancamiento ni ventas en corto en el MVP. Una posición a la vez por estrategia.
2. **El motor de riesgo es obligatorio** para toda orden y no depende de la estrategia ni del LLM.
3. **Las compras se filtran** por: kill switch, pérdida diaria máxima, posiciones abiertas máximas y tamaño máximo por orden (por defecto 2 % del capital, 3 % de pérdida diaria, 3 posiciones).
4. **Las ventas siempre se permiten** (si cantidad y precio son válidos): reducen exposición, y bloquearlas podría dejar atrapado al usuario en una pérdida.
5. **Kill switch persistente**: el estado se escribe en disco de forma atómica, se relee en cada orden y un archivo ilegible cuenta como activado. Activarlo nunca depende de la auditoría; liberarlo sí (exige clave y rastro).
6. **Sin rastro no se abre exposición**: si hay auditoría configurada y falla, la compra se deniega.
7. El capital del motor es de solo lectura desde fuera (`set_equity`), para que el backtest no mute estado interno.

## Alternativas descartadas

- **Frenar también las ventas con el kill switch**: riesgo de bloquear la salida en una caída.
- **Límites solo dentro de la estrategia**: una regla errónea o alucinada se saltaría el control.
- **Cuenta con margen/futuros desde el inicio**: la pérdida puede superar el capital; se reconsidera con evidencia en paper.

## Consecuencias

- Con los valores por defecto el tamaño efectivo por orden es ≤ 2 % del capital: los retornos de un backtest salen pequeños a propósito. Subirlo es una decisión explícita (`--max-order-pct` y `size_pct`).
- El kill switch **no** cierra posiciones ni cancela órdenes del exchange; eso es trabajo de `execution` y, mientras tanto, humano.
- **Stop-loss por posición:** cada estrategia lleva `stop_loss_pct` (por defecto 1 %). Es una salida más, así que pasa por el motor de riesgo como cualquier venta. En el backtest se simula con el mínimo de la vela; en vivo habrá que respaldarlo con una orden stop en el exchange (pendiente en `execution`), porque un chequeo cada hora no protege entre velas.
- Falta aún un freno automático por drawdown acumulado (pendiente; ver modelo de amenazas T14).
