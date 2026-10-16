# ADR 0004: Persistencia por tipo de dato y dependencias mínimas

- **Estado:** aceptado (reemplaza parcialmente el ADR 0001 en lo que respecta a PostgreSQL y FastAPI)
- **Fecha:** 2026-10-15

## Contexto

El ADR 0001 decidió usar PostgreSQL y FastAPI, y el proyecto declaraba `sqlalchemy`, `alembic`, `fastapi`, `uvicorn` y `httpx` como dependencias. La auditoría del código mostró que **ninguna se usaba**: cada dependencia es superficie de ataque y de mantenimiento sin beneficio.

## Decisión

Cada tipo de dato usa el almacenamiento más simple que cumple su función:

| Dato | Almacenamiento | Motivo |
|---|---|---|
| Velas de mercado | SQLite (stdlib) en `data/candles.db` | Es caché regenerable; sin servidor. |
| Estrategias y aprobaciones | Archivos JSON versionados | Legibles, diferenciables con git, sin servicio. |
| Auditoría | JSONL de solo-añadir con cadena HMAC | Verificable de forma independiente; un registro por línea. |
| Kill switch | JSON atómico en `data/kill.json` | Debe leerlo otro proceso en cada orden, sin dependencias. |

- **Dependencias directas actuales:** `pydantic` y `ccxt`. Desarrollo: `pytest`, `pytest-cov`, `ruff`, `mypy`, `pip-audit`.
- **Versiones fijadas** en `constraints.txt` (`pip install -c constraints.txt -e ".[dev]"`) y `pip-audit` en CI.
- **PostgreSQL queda reservado**: `docker-compose.yml` se conserva, pero nada lo usa. Se incorporará (con su capa de acceso y migraciones) cuando exista un dato que lo justifique, por ejemplo historial de órdenes y posiciones con consultas, o múltiples procesos escritores. `fastapi`/`uvicorn` se añadirán con el panel web.

## Alternativas descartadas

- **Mantener las dependencias "para después"**: crece la superficie de ataque y se pudren sin uso.
- **Todo en SQLite/Postgres, incluso la auditoría**: una tabla editable con SQL es más fácil de alterar sin dejar rastro que un archivo encadenado y firmado.

## Consecuencias

- Instalación más pequeña y auditable (60 paquetes con todo el entorno de desarrollo).
- Los archivos de estado viven en `data/` y `strategies/`: hay que respaldarlos (ver runbook) y mantenerlos fuera de carpetas sincronizadas si contienen información sensible.
- Pendiente: instalar con hashes por paquete (`pip --require-hashes`) para endurecer la cadena de suministro; hoy solo se fijan versiones.
