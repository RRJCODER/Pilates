# Pilates: agente de IA para estudios de pilates

Un asistente por WhatsApp que gestiona reservas, clientes y bonos, cobros y marketing de un estudio de pilates. Lo forman un **orquestador** con **subagentes** especialistas y **memoria** por cliente.

El proyecto sigue **Spec-Driven Development**: todo empieza en [`specs/`](specs/).

| Documento | Contenido |
|---|---|
| [00 · Constitución](specs/00-constitucion.md) | Reglas que no se negocian (SDD, privacidad, seguridad) |
| [01 · Requisitos](specs/01-requisitos.md) | Qué hace el sistema, con IDs y preguntas abiertas |
| [02 · Diseño](specs/02-diseno.md) | Arquitectura, agentes, herramientas, memoria y datos |
| [03 · Escenarios](specs/03-escenarios.md) | Criterios de aceptación y evals |
| [04 · Plan de tareas](specs/04-plan-tareas.md) | Fases de implementación |

Producto **genérico**: cada estudio se configura en `config/estudio.toml` y elige sus proveedores (pagos, facturación, canal) con variables de entorno.

Estado: **fase 0 completada** (esqueleto, configuración, base de datos y estudio de ejemplo). Siguiente: fase 1, servicios de negocio.

## Arranque rápido

```bash
cp .env.example .env
docker compose up -d db redis
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"
alembic upgrade head
python -m app.db.seed --demo-clientes 30
pytest
```
