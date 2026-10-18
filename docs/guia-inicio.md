# Guía de inicio (para quien empieza)

## Qué es esto

Un bot que opera **con dinero simulado** (paper trading) siguiendo reglas que tú apruebas. No hay riesgo real
de perder dinero y **nada de esto es asesoría financiera**. El bot no inventa estrategias: solo ejecuta las que
tú leíste y aprobaste.

## Uso diario: un doble clic

1. Abre la carpeta del proyecto y haz doble clic en **`iniciar.bat`**.
2. Te pide tu **clave de integridad** (no se ve al escribirla y no se guarda en ningún archivo). Pégala desde tu
   gestor de contraseñas. Si no tienes una, escribe `NUEVA` y el menú te guía.
3. Elige una opción con su número. Todo está en español y pregunta antes de hacer algo importante.

## El camino normal, paso a paso

| Paso | Opción del menú | Qué pasa |
|---|---|---|
| 1. Idea | **3** Crear una estrategia | Escribes una frase; una IA la convierte en reglas **sin aprobar**. |
| 2. Leer | (sale al crearla) | Se muestra qué hace, en español. Léela: ¿es lo que querías? |
| 3. Probar | (te lo ofrece) | El **backtest** simula el último año. Un buen pasado no garantiza el futuro. |
| 4. Aprobar | (te lo ofrece) | Escribes `APROBAR`. Es tu único control: sin aprobación el bot no opera. |
| 5. Iniciar | **5** Iniciar el bot | Se abre en otra ventana. Revisa una vez por cada vela cerrada. |
| 6. Vigilar | **2** Panel en vivo | Precio, posición, señales y eventos. Solo lectura. |
| 7. Frenar | **6** Freno de emergencia | El bot deja de comprar. No vende lo que ya tienes. |

Ejemplo de frase para el paso 1:
`compra BTC/USDT cuando el RSI de 14 baje de 30 y vende cuando suba de 55, velas de 4h, usa 2% del capital`

Incluye siempre el par (BTC/USDT), cuándo comprar, cuándo vender, el tamaño de vela y el % del capital. Si falta
algo, el modelo te lo pedirá en vez de inventarlo.

## Antes de arrancar: ver qué estrategia se aplicará, y editarla

Al elegir «Iniciar el bot» el menú te muestra primero un **pre-vuelo**: qué estrategia usará el bot, **por qué
esa** (la versión más reciente con tu aprobación firmada), de dónde salió (tu frase original y las ediciones),
sus reglas en español y los límites de riesgo. Si hay una versión más nueva sin aprobar, te avisa de que el bot
la ignora. Entonces eliges: iniciar, editar o cancelar.

Editar **nunca modifica** lo aprobado: crea una versión nueva sin aprobar que debes probar (backtest) y aprobar.

- Tamaño por operación: `tradebot edit NOMBRE --size 3`
- Stop-loss: `tradebot edit NOMBRE --stop-loss 2.5`
- Reglas con una frase (usa la IA, que solo propone): `tradebot edit NOMBRE --phrase "sube el umbral del RSI a 35"`
- Ver el pre-vuelo sin arrancar: `tradebot review NOMBRE`

Un bot que ya está corriendo sigue con la versión anterior hasta que lo reinicies.

## Dejarlo corriendo en tu PC

- Mientras el bot corra, el PC debe estar **encendido y despierto**: en Windows, Configuración → Sistema →
  Energía → «Suspender» en **Nunca** (con corriente). Si se duerme, el bot se detiene.
- No cierres la ventana del bot. Si la cierras o reinicias, vuelve a iniciarlo con el menú (opción de iniciar).
- Los avisos quedan en `data\alerts.log`. Para recibirlos en el móvil, crea un bot de Telegram (@BotFather) y,
  en la consola donde arrancas el bot, define `$env:TRADEBOT_TELEGRAM_TOKEN` y `$env:TRADEBOT_TELEGRAM_CHAT`.
- Si el capital cae un 10 % desde su máximo, el bot bloquea las compras por sí solo y te avisa.
- Nada te avisa si el bot se cae por completo (PC apagado, ventana cerrada): mira el panel de vez en cuando.

## ¿La estrategia se sostiene en el tiempo?

En «Estrategias → e) Evaluar por tramos» (o `tradebot evaluate NOMBRE`) se prueba la misma estrategia en 4
periodos seguidos. Si gana solo en uno, probablemente fue suerte. Con menos de 30 operaciones no concluye nada.

## Cómo leer el panel en vivo

- **Bot ● VIVO:** el bot late a tiempo. **● SIN LATIDO:** no responde; mira su ventana.
- **Precio y mini-gráfico:** las últimas 48 velas. El precio en vivo es solo informativo.
- **Posición:** lo que tienes comprado, su ganancia o pérdida latente y a qué distancia está el stop-loss.
- **Señales:** cada condición de la regla con el valor de hoy. `[38.40 vs 55.00]` significa "el RSI está en 38.40 y
  la regla pide más de 55". ✓ = se cumple, ✗ = no se cumple todavía.
- Es normal pasar **horas o días sin que pase nada**: las estrategias lentas operan poco.

## Si algo sale mal

| Ves | Qué hacer |
|---|---|
| "No se pudo generar la estrategia" | Falta algo en tu frase (el par, cuándo vender). Reescríbela con más detalle. |
| "invalid x-api-key" | La clave de Anthropic es incorrecta: créala de nuevo en console.anthropic.com. |
| "credit balance is too low" | Compra créditos de la API en console.anthropic.com → Plans & Billing. |
| "SIN LATIDO" | El bot no está corriendo: opción 5. Si se detuvo solo con fallos seguidos, el freno quedó activo: opción 7 cuando entiendas por qué. |
| Perdiste la clave de integridad | Escribe `NUEVA` al abrir el menú: habrá que aprobar de nuevo tus estrategias. |
| No sabes qué hacer | Opción **6** (freno de emergencia) y pídeme ayuda con lo que viste en pantalla. |

## Tus claves

- **Clave de integridad** (`TRADEBOT_KEY`): firma tus aprobaciones y la auditoría. Se crea una vez y se guarda en un
  **gestor de contraseñas**. Si la pierdes, no se recupera: se crea otra y se aprueba de nuevo.
- **Clave de Anthropic** (`ANTHROPIC_API_KEY`): solo para convertir frases en estrategias. Se crea en
  console.anthropic.com; ponle un tope de gasto mensual.
- Nunca pegues una clave en un chat, un correo ni un archivo del proyecto.

## Glosario

Está también dentro del programa (opción **8**).

- **Paper trading:** operar con dinero simulado y precios reales.
- **Estrategia / versión:** las reglas escritas; cada cambio es una versión nueva y las aprobadas no se modifican.
- **Aprobación:** tu firma de que leíste la regla y autorizas usarla.
- **Backtest:** simulación del pasado. No garantiza el futuro.
- **Vela:** resumen de un periodo (1 h, 4 h): apertura, máximo, mínimo y cierre.
- **RSI:** indicador de 0 a 100. Bajo 30, "sobrevendido"; sobre 70, "sobrecomprado".
- **Media móvil:** promedio del precio de las últimas N velas.
- **Stop-loss:** si el precio cae ese % desde la compra, el bot vende para limitar la pérdida.
- **Posición:** lo que tienes comprado ahora.
- **P&L:** ganancia o pérdida (latente = si vendieras ahora; realizada = ya vendida).
- **Kill switch:** freno de emergencia: el bot deja de comprar.
- **Auditoría:** diario inalterable de todo lo que pasó.
