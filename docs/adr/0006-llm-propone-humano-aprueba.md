# ADR 0006: El LLM propone, el humano aprueba, el código ejecuta

- **Estado:** aceptado
- **Fecha:** 2026-10-15

## Contexto

El usuario quiere dar directrices en lenguaje natural. Un modelo de lenguaje puede alucinar, equivocarse de indicador o ser manipulado por texto hostil (prompt injection). Con dinero real, eso no puede acercarse a una orden.

## Decisión

1. **El LLM solo traduce** una directriz a un JSON del esquema `Strategy`. No ve claves ni datos de la cuenta, no llama a brokers y no habla con el motor de riesgo.
2. **La salida se trata como no confiable**: se extrae el JSON, se valida con el esquema estricto (campos desconocidos, tamaños, stop, topes de anidación) y, si falla, se reintenta como máximo 3 veces enviando el error del validador. Si el modelo responde `{"error": ...}`, se respeta y no se reintenta.
3. **La directriz va delimitada como datos** (`<directiva>...</directiva>`); el delimitador no puede cerrarse desde dentro. Límite de 2000 caracteres de entrada y 20000 de respuesta.
4. **Se guarda SIN aprobar** como versión nueva. Aunque la directriz diga "apruébala", `propose` nunca aprueba. Si el modelo reutiliza el nombre de una estrategia existente, solo crea una versión nueva sin aprobar; `load_approved` sigue usando la versión aprobada anterior.
5. **La descripción que lee el humano la genera código** (`strategy/describe.py`) a partir del esquema validado, nunca el modelo, para que una explicación engañosa no pueda inducir a aprobar otra cosa.
6. **Todo queda en la auditoría**: `llm.propose` con hash de la directriz, un extracto, número de intentos y resultado (también los fallos).
7. **Proveedor intercambiable**: la interfaz `LlmClient` tiene un adaptador para la API de Claude (`pip install -e ".[llm]"`, `ANTHROPIC_API_KEY`, modelo en `TRADEBOT_LLM_MODEL`). Los tests usan un modelo falso, sin red ni costo.

## Alternativas descartadas

- **Dejar que el LLM escriba código o llame al broker:** imposible de auditar con garantías.
- **Pedir al modelo que explique su propia regla:** la explicación y la regla pueden divergir.
- **Aprobar automáticamente si el backtest es bueno:** un backtest bueno no prueba nada con pocas operaciones y la aprobación es el control humano.

## Consecuencias

- Cada directriz cuesta hasta 3 llamadas pequeñas; no hay bucles automáticos. Falta un tope mensual (hoy se controla en la consola del proveedor).
- La calidad de la traducción depende del modelo; el validador impide reglas inválidas, no reglas **malas**. Un humano debe leer la descripción y revisar el backtest.
- Pendiente: probar con el modelo real la resistencia a prompt injection y registrar los resultados antes de dinero real.
