# ADR 0001: Arquitectura base del bot de trading

- **Estado:** aceptado; PostgreSQL y FastAPI como dependencias actuales quedan reemplazados por el [ADR 0004](0004-persistencia-y-dependencias-minimas.md) (se incorporarán cuando se necesiten)
- **Fecha:** 2026-10-15

## Contexto

Se necesita un bot de trading al que el usuario le dé directrices de estrategia por chat/panel web, como si instruyera a un trader humano. Un solo desarrollador, plazo de un mes para una primera versión en paper trading, datos gratuitos, montos parametrizables.

## Decisiones

1. **Monolito modular** en Python 3.11+, con FastAPI (panel web) y PostgreSQL. Un solo desarrollador y un solo dominio no justifican microservicios.
2. **MVP:** cripto spot, swing o intradía lento (velas de 1h/4h). Acciones, forex y futuros quedan para fases posteriores, vía nuevos adaptadores.
3. **Adaptadores por puertos** (`Broker`, `MarketData`): agregar un mercado es escribir un adaptador, no cambiar el núcleo.
4. **Directrices en lenguaje natural → reglas estructuradas y versionadas.** Un LLM propone la regla, el usuario la aprueba y un motor determinista la ejecuta. El LLM nunca envía órdenes.
5. **Motor de riesgo independiente** con límites duros (tamaño por operación, pérdida diaria, posiciones abiertas, kill switch). Toda orden pasa por él. En el MVP (spot) las ventas siempre se permiten porque reducen exposición; las compras son las que se filtran.
6. **Paper trading por defecto.** Pasar a dinero real requiere activarlo de forma explícita y es una decisión posterior a semanas de paper.
7. **Auditoría:** cada orden, decisión de riesgo y cambio de estrategia registra quién, qué, cuándo y resultado.
8. **Infraestructura:** local con Docker durante el mes 1; VPS desde la semana 4, cuando el bot deba correr 24/7 en paper. Se contrata por continuidad operativa, no por latencia.

## Alternativas descartadas

- **ML entrenado con históricos:** opaco, propenso a sobreajuste, difícil de auditar.
- **LLM decidiendo cada operación en vivo:** lento, no determinista y riesgoso con dinero real.
- **Microservicios / serverless:** costo operativo y de lock-in sin beneficio a esta escala.
- **Telegram como interfaz:** saca las directrices a servidores de terceros.

## Consecuencias

- El núcleo es auditable y testeable sin red ni dinero.
- Un mes alcanza para un bot operando en paper, no para una estrategia probada.
- Los datos gratuitos tienen límites de llamadas; el módulo `data` debe cachear históricos localmente.
