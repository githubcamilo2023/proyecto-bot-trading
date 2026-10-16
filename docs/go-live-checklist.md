# Lista de verificación para pasar a dinero real

**Regla:** no se pasa a dinero real hasta que **todos** los puntos de las secciones A–E estén marcados con evidencia (fecha, enlace o comando). Si uno falla, se vuelve a paper. Este documento se rellena a mano; marcar sin evidencia no cuenta.

Hoy el proyecto está en la fase de cimientos: la mayoría de estos puntos **no se pueden cumplir todavía** (no existen `execution`, `llm` ni panel). Esa es la razón de que la lista exista ahora.

## A. Prerrequisitos técnicos

- [x] Módulo `execution` con broker en modo paper y orden idempotente (reintentar no duplica). *Hecho; falta el broker real.*
- [ ] Reconciliación al arrancar: el estado local se contrasta con posiciones y órdenes reales; si no coincide, el bot no opera y avisa.
- [x] Espera creciente y freno automático (kill switch) ante fallos repetidos. *Hecho en el bucle; falta probarlo contra un exchange real.*
- [x] Validación de datos: huecos de velas, precios absurdos y datos viejos detienen las señales.
- [ ] Reglas de lote y notional mínimo del exchange respetadas (hoy la cantidad usa 8 decimales fijos).
- [ ] Stop-loss respaldado por una orden stop real en el exchange (el esquema ya lo tiene, 3 % por defecto) y kill switch automático por drawdown.
- [ ] Anclaje periódico del `head()` de la auditoría fuera del archivo (detecta truncados).
- [ ] Alertas externas (caída, rechazos repetidos, kill switch activado). *El latido y `tradebot status` ya existen; falta que algo AVISE sin que lo consultes.*
- [ ] CI en verde en la rama principal: ruff, mypy `--strict`, tests con cobertura ≥ 90 %, `pip-audit` sin hallazgos.

## B. Evidencia de paper trading

- [ ] Al menos **8 semanas continuas** de paper en el VPS (o la duración que decidas, escrita aquí antes de empezar: ______).
- [ ] Al menos **30 operaciones cerradas** por estrategia; con menos, el resultado no prueba nada.
- [ ] Resultado en paper comparado con el backtest del mismo periodo; diferencias grandes explicadas (comisiones, deslizamiento, latencia).
- [ ] Revisión de cada orden rechazada por el riesgo y de cada caída del bot: causa y corrección anotadas.
- [ ] `tradebot audit-verify` íntegro al cierre del periodo.
- [ ] Simulacro de emergencia hecho al menos una vez: `kill` con el bot en marcha, comprobar que deja de comprar, `release`.

## C. Seguridad

- [ ] Modelo de amenazas revisado después de construir `execution`/`llm`/panel; todos los ⬜ de esas fases resueltos o aceptados por escrito.
- [ ] Claves del exchange: **sin permiso de retiro**, con lista de IPs permitidas, fuera del repositorio y de OneDrive.
- [ ] Clave de integridad rotada desde la usada en pruebas; guardada en un gestor de contraseñas.
- [ ] Panel web (si se expone): autenticación, roles, límite de tasa, TLS, sin exposición directa a internet sin proxy.
- [ ] Prueba de prompt injection sobre el módulo `llm` **con el modelo real** (los tests actuales usan un modelo falso): ninguna directriz hostil produce una regla que se ejecute sin aprobación.
- [ ] Tope mensual de gasto en la consola del proveedor de la API.
- [ ] Revisión por una segunda persona del código de `risk`, `execution` y `integrity`.

## D. Límites de capital

- [ ] Capital inicial en vivo escrito aquí: ______ (cifra que puedo perder completa sin afectar mi vida).
- [ ] Tamaño máximo por orden y pérdida diaria máxima definidos en cifras reales, no solo en porcentajes: ______ .
- [ ] Modo live exige una activación explícita y separada (variable y confirmación), nunca por defecto.
- [ ] Primer mes en vivo con el **10 %** o menos del capital previsto; escalar solo con resultados y sin incidentes.

## E. Operación

- [ ] Runbook probado: kill, release, rotación de clave y recuperación de auditoría ejecutados al menos una vez.
- [ ] Copia de seguridad restaurada con éxito en una prueba.
- [ ] Persona o canal que recibe las alertas, y qué hacer si no estoy disponible.
- [ ] Revisión de obligaciones legales y fiscales del lugar de residencia antes de operar con dinero real.

## Firma

| Fecha | Quién revisó | Decisión (adelante / volver a paper) | Notas |
|---|---|---|---|
| | | | |
