# Pilates: agente de IA para estudios de pilates

Un asistente por **WhatsApp** que gestiona un estudio de pilates. Un **orquestador** entiende cada mensaje y lo delega en cuatro **subagentes** especialistas (Reservas, Clientes y bonos, Cobros, Marketing), con **memoria** por cliente. Está hecho en Python con FastAPI, el SDK oficial `anthropic` (Tool Runner), PostgreSQL y Redis/arq.

Es un producto **genérico**: cada estudio se configura en `config/estudio.toml` y elige sus proveedores (pagos, facturación, canal) con variables de entorno.

## Planes

| | **Básico** | **IA** |
|---|---|---|
| Menús y botones de WhatsApp (reservar, cancelar, bono, pagar, alta…) | ✅ | ✅ |
| Recordatorios, lista de espera, impagos, cumpleaños, informe semanal | ✅ | ✅ |
| Escribir en lenguaje libre, memoria de preferencias, campañas redactadas | — | ✅ |
| Coste de IA | 0 | Solo cuando se escribe texto libre |

Se elige con `plan = "basico"` o `plan = "ia"` en `[estudio]`. En el plan IA, los botones también se resuelven sin IA.

El proyecto sigue **Spec-Driven Development**: todo empieza en [`specs/`](specs/).

| Documento | Contenido |
|---|---|
| [00 · Constitución](specs/00-constitucion.md) | Reglas que no se negocian (SDD, privacidad, seguridad) |
| [01 · Requisitos](specs/01-requisitos.md) | Qué hace el sistema, con IDs |
| [02 · Diseño](specs/02-diseno.md) | Arquitectura, agentes, herramientas, memoria y datos |
| [03 · Escenarios](specs/03-escenarios.md) | Criterios de aceptación y evals |
| [04 · Plan de tareas](specs/04-plan-tareas.md) | Estado de cada tarea |
| [Puesta en marcha](docs/puesta-en-marcha.md) | WhatsApp, Stripe, despliegue y piloto |

## Arranque rápido

```bash
cp .env.example .env                       # pon tu ANTHROPIC_API_KEY
docker compose up -d db redis
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"
alembic upgrade head
python -m app.db.seed --demo-clientes 30
python -m app.chat --como +34600000001     # hablar como admin (escribe el número para pulsar un botón)
```

## Tests

```bash
pytest                                      # unitarios + agentes con Claude simulado
TEST_DATABASE_URL=postgresql+psycopg://pilates:pilates@localhost:5432/pilates_test pytest  # + concurrencia
ANTHROPIC_API_KEY=... pytest tests/evals -s # evals con Claude real (cuestan dinero)
```

## Estado

Las fases 0 a 4 están implementadas y probadas: servicios, agentes, memoria, WhatsApp, pagos y cron. Falta:
- **T3.3**: lanzar los evals con una API key real para medir la latencia y el coste.
- **Fase 5**: dar de alta en Meta, desplegar y hacer el piloto con un estudio real (guía en `docs/`).
