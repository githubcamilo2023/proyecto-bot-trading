# ADR 0007: Ejecución en paper con órdenes idempotentes y freno automático

- **Estado:** aceptado
- **Fecha:** 2026-10-15

## Contexto

Antes de tocar dinero real hay que demostrar semanas de operación continua simulada con datos reales. Un bot que corre 24/7 se reinicia, pierde la red, recibe datos defectuosos y puede ser manipulado por archivos alterados. El módulo debe comportarse bien ante todo eso, y debe poder sustituirse por un broker real sin reescribir la lógica.

## Decisión

1. **Un ciclo (`Bot.tick`) por vela cerrada**, con este orden: validar datos → stop-loss → comprobar que la estrategia sigue aprobada → señal → riesgo → orden. El bucle espera al cierre de cada vela.
2. **Validación de datos antes de operar** (`data/validate.py`): suficientes velas, ordenadas, sin huecos, precios coherentes, sin saltos mayores al 50 % y con la última vela como máximo una vela atrasada. Si algo falla, ese ciclo no opera y queda auditado.
3. **Órdenes idempotentes**: el id de cada orden se deriva de estrategia + versión + vela (`siempre-v1-buy-20261015T1200`). Repetir el ciclo o reiniciar el proceso sobre la misma vela no vuelve a ejecutar la orden.
4. **Estado del broker en disco, atómico y fail-closed**: efectivo, posición, órdenes ejecutadas y capital al inicio del día. Se guarda ANTES de dar la orden por hecha; un archivo ilegible o incoherente impide arrancar; una posición de otro símbolo también.
5. **Solo estrategias aprobadas**: se carga con `load_approved` y se revalida en cada ciclo. Si el archivo cambia con el bot en marcha, no se abren posiciones nuevas, pero el stop-loss sigue vigente (salir siempre se puede) y se audita `strategy.tamper_suspected` una sola vez.
6. **Freno automático**: tras 5 fallos seguidos de ciclo (red, datos, excepciones) se activa el kill switch persistente, se audita `bot.halt` y el bucle termina con código 3. Entre fallos hay espera creciente (10 s, 20 s... hasta 5 min). Un ciclo exitoso reinicia la cuenta.
7. **Latido** (`data/heartbeat.json`) con la hora esperada del siguiente ciclo; `tradebot status` avisa si el bot no late a tiempo.
8. **Modo real inexistente**: `TRADING_MODE` distinto de `paper` se rechaza. Activar dinero real exigirá escribir y revisar un broker real, no cambiar una variable.
9. **Contrato `Broker`** en `ports.py`: el bot depende de la interfaz, no de `PaperBroker`.

## Simulación: lo que se aproxima

- Las órdenes se llenan al **cierre de la vela que originó la señal** más deslizamiento y comisión (aproxima la apertura de la siguiente). En real el precio será otro.
- El stop-loss se evalúa **al cierre de cada vela** con su mínimo, y llena al nivel del stop o a la apertura si hubo hueco (igual que el backtest). Entre velas no hay protección: en real hace falta una orden stop puesta en el exchange.

## Alternativas descartadas

- **Reintentar indefinidamente ante fallos:** un bot ciego operando con datos viejos es peor que un bot detenido.
- **Estado solo en memoria:** un reinicio duplicaría o perdería posiciones.
- **Base de datos para el estado:** innecesaria con un solo proceso escritor (ver ADR 0004).

## Consecuencias

- Una posición abierta y un solo símbolo por bot. Varias estrategias requerirán varios procesos o ampliar el estado.
- El kill switch tras fallos repetidos exige intervención humana (`tradebot release`): es deliberado.
- Pendiente para dinero real: broker real con reconciliación contra el exchange, orden stop en el exchange, reglas de lote y notional mínimo, y alertas externas.
