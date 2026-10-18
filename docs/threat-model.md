# Modelo de amenazas

Estado: vigente para la fase previa a `llm`, `execution` y panel web. Se revisa antes de cada una de esas fases.

## 1. Qué protegemos (activos)

| Activo | Por qué importa |
|---|---|
| Capital en el exchange (futuro) | Es lo único que puede perderse de forma irreversible. |
| Claves API del exchange (futuro) | Quien las tiene opera con tu cuenta. |
| `TRADEBOT_KEY` (clave de integridad) | Con ella se pueden forjar aprobaciones y registros de auditoría. |
| Estrategias aprobadas | Son las únicas reglas con permiso para mover dinero. |
| Registro de auditoría | Es la evidencia de qué pasó, quién lo hizo y por qué. |
| Estado del kill switch | Es el freno de emergencia. |

## 2. Quién puede hacer daño (actores)

1. **Yo mismo, por error**: editar un JSON aprobado, aprobar sin leer, dejar un `.env` en el repo.
2. **El LLM (futuro)**: puede alucinar una regla peligrosa o ser manipulado por texto hostil (prompt injection) que le llegue como "directriz".
3. **Quien acceda a archivos sin ser yo**: sincronización en la nube (OneDrive), copias de seguridad, un compañero de equipo, malware con acceso de lectura/escritura a la carpeta pero **sin** la clave en memoria.
4. **Una dependencia comprometida** (cadena de suministro de PyPI).
5. **Un atacante con control total de mi sesión de usuario** (puede leer la clave del entorno). **Fuera de alcance**: ningún mecanismo local lo detiene; solo limita el daño (claves del exchange sin permiso de retiro, límites de riesgo, paper por defecto).

## 3. Fronteras de confianza

```
 Texto del usuario ──► [LLM, no confiable] ──► JSON de regla ──► validación estricta ──► APROBACIÓN HUMANA firmada
                                                                                              │
 Datos de mercado (red, no confiables) ──► validación ──► motor de estrategia ──► motor de riesgo ──► broker
                                                                                  (sin LLM en este camino)
```

Reglas que se mantienen:

- El LLM **nunca** llama a un broker ni al motor de riesgo: solo produce un JSON que debe pasar la validación y la aprobación.
- Lo que no tiene aprobación válida **no se ejecuta** (`StrategyStore.load_approved`).
- Toda compra pasa por el motor de riesgo; el motor no depende del LLM ni de la estrategia.

## 4. Amenazas y estado de las defensas

Leyenda: ✅ implementado y con tests · 🟡 parcial · ⬜ pendiente (con la fase donde se resuelve).

| # | Amenaza | Defensa | Estado |
|---|---|---|---|
| T1 | Editar una estrategia ya aprobada y que siga contando como aprobada | La aprobación incluye el SHA-256 del archivo y va firmada con HMAC; cualquier cambio la invalida | ✅ |
| T2 | Falsificar o copiar el registro de aprobación (a otra versión, con otro autor) | El registro firmado incluye nombre, versión, hash y autor; se verifica todo | ✅ |
| T3 | Regla enorme o anidada hasta agotar CPU/memoria | Tope de 6 niveles y 50 comparaciones, periodos 1–500, esquema estricto (`extra="forbid"`) | ✅ |
| T4 | Path traversal por el nombre de la estrategia (`../`) | Nombre validado con regex antes de tocar el disco | ✅ |
| T5 | Kill switch que se pierde al reiniciar o que no se puede activar desde otra consola | Estado en disco, releído en cada orden; archivo ilegible = activado; `kill` funciona sin clave | ✅ |
| T6 | Reabrir el trading sin dejar rastro | `release` exige clave y auditoría; si no se puede auditar, no se libera | ✅ |
| T7 | Alterar o borrar el historial de acciones | Auditoría JSONL con cadena de hashes firmada; `tradebot audit-verify` detecta ediciones, borrados del medio, reordenamientos y bifurcaciones | ✅ |
| T8 | Operar sin rastro de auditoría | Compra denegada si la auditoría falla; las ventas siguen permitidas (reducen exposición) | ✅ |
| T9 | Secretos subidos al repositorio | `.gitignore`, guardia `guard_secrets` en pre-commit, `.env` fuera de la carpeta sincronizada | 🟡 (depende de que se instale pre-commit; un escáner dedicado como gitleaks sería mejor) |
| T10 | Dependencia vulnerable o comprometida | `constraints.txt` con versiones exactas, `pip-audit` en CI, dependencias mínimas (2 directas) | 🟡 (sin hashes por paquete; ver ADR 0004) |
| T11 | Datos de mercado corruptos o con huecos producen señales falsas | Solo velas cerradas; validación de huecos, desorden, retraso, precios incoherentes y saltos > 50 %: si falla, no se opera ese ciclo | ✅ |
| T12 | Exchange caído o respuestas lentas bloquean el bot | Espera creciente entre fallos; tras 5 seguidos se activa el kill switch y el bot se detiene; latido visible con `status` | 🟡 (sin alertas externas; el exchange real aún no existe) |
| T13 | Orden duplicada tras un reinicio, o estado local distinto del real | Órdenes con id determinista por vela (idempotentes), estado atómico en disco, arranque rechazado si el estado es ilegible o de otro símbolo | 🟡 (paper sí; con un broker real falta reconciliar contra el exchange) |
| T14 | Pérdida acumulada que no frena el bot | Límite de pérdida diaria (BUY denegado); stop-loss por posición del 3 % en el esquema (simulado en backtest; en vivo falta la orden stop en el exchange); **no** hay kill switch automático por drawdown | 🟡 fase `execution` |
| T15 | Prompt injection / regla alucinada | Esquema cerrado y validado en código; reintentos acotados; resultado guardado SIN aprobar; descripción para el humano generada por código; aprobación humana firmada; límites duros del riesgo; el LLM no ve claves ni ejecuta. Ver ADR 0006 | 🟡 implementado; falta una prueba con un modelo real y presupuesto de uso |
| T20 | Gasto descontrolado de la API del LLM | Un comando = hasta 3 llamadas de ≤ 1500 tokens de salida; directriz ≤ 2000 caracteres; sin bucles automáticos. Falta tope mensual | 🟡 |
| T16 | Acceso no autorizado al panel web | Sin panel todavía; exigirá autenticación, roles, límite de tasa y TLS | ⬜ fase `api` |
| T17 | Claves de exchange robadas | Solo permiso de lectura+trading, **sin retiro**, con lista de IPs; nunca en el repo ni en OneDrive | ⬜ se configura al conectar el exchange |
| T18 | Truncar el final del registro de auditoría | No se detecta solo; hay que guardar `head()` (seq + mac) en otro lugar | ⬜ fase `execution` (anclaje periódico) |
| T19 | Dos procesos escribiendo la auditoría a la vez bifurcan la cadena | Se detecta al verificar, pero no se evita | 🟡 (un solo escritor por diseño) |

## 5. Límites honestos de este diseño

- La clave HMAC y los archivos están en la **misma máquina**. Esto protege contra errores propios, sincronización/copias y quien tenga los archivos pero no la clave; **no** protege contra quien controle tu sesión de usuario (actor 5).
- `TRADEBOT_KEY` en una variable de entorno persistente de Windows queda en el registro del usuario, legible por procesos de ese usuario. Es un compromiso aceptable para paper trading; para dinero real conviene un gestor de secretos o una cuenta de servicio dedicada.
- El kill switch frena **compras nuevas**. No cierra posiciones abiertas ni cancela órdenes en el exchange (no existe esa integración todavía). Cerrar posiciones sigue siendo una decisión humana.
- En Windows, leer `data/kill.json` justo mientras se reemplaza puede fallar un instante; el motor lo trata como "ilegible = activado" y frena compras. Es un falso positivo seguro (se resuelve solo en la siguiente orden), no un hueco.
- Los tests prueban el comportamiento ante estos ataques con archivos manipulados, pero no sustituyen una revisión externa antes de usar dinero real.

## Pre-vuelo y edición (`review`, `edit`)

- **Editar no aprueba**: `tradebot edit` y la edición del menú solo crean una versión NUEVA sin aprobar; la
  aprobada no se toca y el bot sigue usando la última versión con firma válida hasta que tú apruebes y reinicies.
- **La IA solo propone** también al editar con `--phrase`: su salida pasa por el mismo esquema estricto y queda
  sin aprobar.
- **`review` es de solo lectura**: lee archivos de estrategia, la firma y el final de la auditoría (máx. 5 MB).
  El texto que viene de la auditoría se limpia de caracteres de control antes de mostrarse (evita secuencias de
  escape que alteren la consola).
- **Causa visible**: si una firma no coincide (otra clave, archivo cambiado) el mensaje lo dice en vez de
  «sin aprobar».

## Avisos (Telegram) y caída máxima

- **El token de Telegram** vive solo en variables de entorno de la consola (como la clave de integridad), nunca en
  archivos ni en el repo. Los fallos de envío se registran sin la URL (que lleva el token).
- **Salida de red nueva**: solo `api.telegram.org` por HTTPS, con tiempo máximo de 10 s, y solo si se configuran
  ambas variables. Un aviso fallido no detiene al bot. El texto del aviso no contiene claves.
- **Caída máxima**: el motor de riesgo guarda el máximo de capital junto al kill switch (`kill.peak`). Si ese
  archivo se borra o se corrompe, el máximo se reinicia con el capital actual (control secundario; el kill switch
  y la pérdida diaria siguen firmes). Activarlo bloquea compras; las ventas y el stop-loss siguen permitidos.
