# ADR 0002: Reglas en JSON estricto y aprobación firmada

- **Estado:** aceptado
- **Fecha:** 2026-10-15

## Contexto

Las directrices del usuario (y, más adelante, la salida de un LLM) deben convertirse en algo que un motor determinista pueda ejecutar y que un humano pueda revisar antes de que mueva dinero. La primera versión guardaba la aprobación como un archivo con "quién y cuándo", sin ligarla al contenido: se podía editar la estrategia aprobada y seguía figurando como aprobada. La auditoría lo demostró.

## Decisión

1. **Esquema cerrado** (pydantic v2, `extra="forbid"`, inmutable): solo existen `close`, `sma`, `ema`, `rsi`; comparadores `< <= > >= crosses_above crosses_below`; combinadores `all`/`any`. Nada de código ni expresiones libres.
2. **Topes explícitos**: 6 niveles de anidación, 50 comparaciones, periodos 1–500 (constantes en `config.py`).
3. **Versiones inmutables** en disco (`v<N>.json`, escritura con modo `"x"`).
4. **Aprobación = registro firmado** con HMAC-SHA256 que incluye nombre, versión, **SHA-256 del contenido**, autor y fecha. Se verifica en cada uso. La clave sale de `TRADEBOT_KEY` (≥ 32 caracteres).
5. **El bot solo ejecuta lo que devuelve `load_approved`**: la versión más reciente con aprobación válida. Si la última no está aprobada, usa la anterior aprobada y lo avisa en el log.
6. Registro inválido, ilegible o de otra versión = **no aprobado** (falla cerrado) y queda `strategy.tamper_suspected` en la auditoría.

## Alternativas descartadas

- **Dejar al LLM generar código Python**: imposible de auditar con garantías.
- **Aprobación como simple marca de archivo**: no protege contra ediciones posteriores.
- **Firma asimétrica (par de claves)**: más robusta si la clave de verificación no debe poder firmar, pero añade gestión de claves desproporcionada para un solo usuario. Se reconsidera si hay más de una persona aprobando.

## Consecuencias

- Editar una estrategia obliga a una versión nueva y a una nueva aprobación: es fricción deseada.
- Sin `TRADEBOT_KEY` no se puede aprobar ni ejecutar (el backtest sí funciona, no necesita aprobación).
- Proteger la clave pasa a ser parte de la seguridad del sistema (ver `docs/threat-model.md`). Rotarla invalida las aprobaciones.
- Añadir indicadores o comparadores exige cambiar el esquema y sus tests; es intencional.
