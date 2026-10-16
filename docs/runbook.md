# Runbook: operación y emergencias

Todos los comandos se ejecutan **desde la raíz del proyecto** con el entorno virtual activo. Las rutas por defecto son relativas (`data/kill.json`, `data/audit.jsonl`, `strategies/`): si ejecutas desde otra carpeta, apuntarás a otro estado y el freno no llegará al bot. Si cambias una ruta en el bot, usa la misma en estos comandos (`--state`, `--audit`).

## 0. Configuración inicial (una vez)

1. Genera la clave de integridad:
   ```
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. Defínela en tu sesión (PowerShell): `$env:TRADEBOT_KEY = "TU_CLAVE"` (reemplaza TU_CLAVE por la clave generada) (sin los símbolos `<` `>`: en PowerShell están reservados).
   Guárdala en un gestor de contraseñas. **No** la pongas en el repositorio ni en una carpeta sincronizada. Nada carga `.env` automáticamente: las variables hay que definirlas en la consola (o en el servicio que ejecute el bot).
3. Comprueba: `tradebot audit-verify` debe decir `Auditoría íntegra: 0 registros verificados`.

## 1. EMERGENCIA: frenar el bot

```
tradebot kill --reason "motivo corto"
```

- Funciona **sin clave** y con el bot en marcha: el estado vive en `data/kill.json` y el motor lo relee en cada orden.
- Bloquea compras nuevas. **No** cierra posiciones ni cancela órdenes abiertas en el exchange: eso se hace a mano en el exchange (aún no hay integración).
- Si dice `ATENCIÓN: no se pudo escribir el estado`, el freno **no** llegó al bot en marcha. Detén el proceso del bot (Ctrl+C o cerrar su consola) y revisa permisos/disco.
- Si dice `Aviso: sin TRADEBOT_KEY ... SIN registro de auditoría`, el freno sí funcionó, pero no quedó anotado. Anótalo tú en tu bitácora.

Para reanudar (exige clave y deja rastro):

```
tradebot release
```

No liberes hasta saber por qué se activó. Revisa primero la sección 3.

## 2. Proponer y aprobar una estrategia

`tradebot propose "..."` guarda una versión **sin aprobar**. Antes de aprobar: `tradebot show rebote-rsi` y un `tradebot backtest rebote-rsi` (cambia `rebote-rsi` por tu nombre).

```
tradebot approve rebote-rsi 1
```

La aprobación queda ligada al contenido exacto. Si editas el archivo después, deja de valer: hay que guardar una versión nueva y aprobarla de nuevo. **Lee el JSON antes de aprobar**; la aprobación es el único control humano antes de que una regla pueda mover dinero.

## 2b. Paper trading continuo

```
tradebot paper rebote-rsi --once     # un solo ciclo, para probar
tradebot paper rebote-rsi            # continuo: un ciclo por vela. Ctrl+C para detener
tradebot status                      # latido, kill switch, efectivo y posición
```

- Solo corre estrategias **aprobadas** y revalida la aprobación en cada ciclo. Exige `TRADEBOT_KEY`.
- El estado vive en `data/paper.json`. Para empezar de cero con otro capital, detén el bot y mueve ese archivo a otra carpeta; el capital inicial solo se usa al crearlo.
- Reiniciar el bot es seguro: no repite órdenes de una vela ya procesada.
- Si el bot se detiene solo con `fallos seguidos`, el kill switch queda activado: revisa la red y los datos, y luego `tradebot release`.
- `status` dice `POSIBLEMENTE CAÍDO` si pasaron más de 2 minutos de la hora esperada del siguiente ciclo. Hoy nada te avisa por sí solo: hay que mirarlo.
- El modo real no existe: `TRADING_MODE` distinto de `paper` se rechaza.

## 3. Algo no cuadra: investigar

| Síntoma | Qué hacer |
|---|---|
| `tradebot audit-verify` dice `AUDITORÍA ALTERADA` (código 4) | 1) `tradebot kill`. 2) **Copia** `data/audit.jsonl` a un lugar seguro sin modificarlo. 3) Mira el número de línea que indica el mensaje. 4) No sigas operando hasta explicar la causa. Un registro cortado por un apagado brusco también aparece como "formato inválido" en la última línea. |
| El bot se niega a comprar: `auditoría no disponible` | No se puede escribir `data/audit.jsonl` (disco lleno, permisos, archivo corrupto en su final). Libera espacio o corrige permisos. Si el final está corrupto: guarda una copia, **no la edites a mano**, y sigue la sección 5. |
| `kill switch activo` sin que lo hayas activado | Mira `data/kill.json` (quién y cuándo). Si el archivo es ilegible, el motor asume "activado" a propósito. Corrige o recrea con `tradebot kill` y luego `tradebot release`. |
| `paper` imprime `skip: ...` | Los datos no eran operables ese ciclo (retraso, hueco, salto raro). No es un fallo del bot: no operó a propósito. Si se repite, revisa el exchange o tu conexión. |
| `paper` termina con `hay una posición abierta en X, distinta de Y` | El estado guardado es de otro símbolo. Detén el bot y revisa `data/paper.json` antes de seguir. |
| `propose` falla con `No se pudo generar la estrategia` | El modelo respondió algo fuera del esquema 3 veces, o dijo que no es expresable. Reformula la directriz con los indicadores disponibles (close, sma, ema, rsi). Queda anotado en la auditoría (`llm.propose`, `error`). |
| Una estrategia no se ejecuta aunque la aprobaste | En el log aparece `no ejecutable: ...`. `la estrategia cambió después de ser aprobada` = el archivo se modificó; `firma ... inválida` = clave distinta o registro alterado (también queda en la auditoría como `strategy.tamper_suspected`). |
| El bot usa una versión anterior | Hay versiones más nuevas sin aprobar: es el comportamiento seguro. Aprueba la nueva o ignora el aviso. |

Los logs salen por la salida de error en JSON, una línea por evento, con `trace_id` para seguir una ejecución. Nivel: `--log-level INFO` o `TRADEBOT_LOG_LEVEL`.

## 4. Rotar la clave de integridad

Cambiar `TRADEBOT_KEY` invalida **todas** las aprobaciones y hace que el registro de auditoría antiguo solo se verifique con la clave antigua.

1. Archiva `data/audit.jsonl` renombrándolo (p. ej. `audit-2026-10.jsonl`) y conserva la clave antigua en el gestor de contraseñas, anotando qué archivo verifica.
2. Define la clave nueva.
3. Vuelve a aprobar cada estrategia que debas ejecutar (`tradebot approve ...`).
4. Si sospechas que la clave se filtró, hazlo de inmediato y trata como no confiable lo aprobado antes.

## 5. Recuperarse de una auditoría con el final corrupto

El motor se niega a añadir registros sobre una cola corrupta (por diseño). Procedimiento:

1. `tradebot kill`.
2. Copia `data/audit.jsonl` a una carpeta de evidencias (no la edites).
3. Renombra el original (p. ej. `audit-corrupto-<fecha>.jsonl`) para que el bot empiece una cadena nueva.
4. Anota en tu bitácora el motivo y los números de secuencia final de la cadena vieja.
5. `tradebot release`.

## 6. Copias de seguridad

Lo que importa respaldar: `strategies/`, `data/audit.jsonl`, `data/kill.json` y la clave (en el gestor de contraseñas, **nunca** junto a la copia de los datos). `data/candles.db` es solo caché: se puede regenerar descargando de nuevo. Prueba restaurar al menos una vez antes de depender de esto.

## 7. Pendiente de operación (se completará con la fase `execution`)

Alertas ante caídas o rechazos repetidos, latido del bot, reconciliación al arrancar, y procedimiento para cerrar posiciones desde el propio bot.
