# Plan de tareas

Cada tarea cita los requisitos que cubre. Una fase no empieza hasta que la anterior pasa sus verificaciones.

## Fase 0: Cimientos
- [ ] T0.1 `pyproject.toml` (fastapi, anthropic, sqlalchemy, alembic, arq, apscheduler, pydantic-settings, httpx, stripe), con ruff, mypy y pytest.
- [ ] T0.2 `docker-compose.yml` con postgres, redis, app y worker.
- [ ] T0.3 `config/settings.py` y `config/estudio.toml` con datos de ejemplo (Q5).
- [ ] T0.4 Modelos SQLAlchemy y la primera migración (diseño §9).
- [ ] T0.5 Script `seed` con el estudio de ejemplo: 3 instructores, 40 clases semanales y 30 clientes.

## Fase 1: Servicios de negocio (sin LLM)
- [ ] T1.1 `servicios/clases.py`: buscar, crear, modificar y cancelar (RF-RES-001, 007).
- [ ] T1.2 `servicios/reservas.py`: reservar con bloqueo, cancelar con la política, recurrentes (RF-RES-002, 005, 006) y test de concurrencia (E-RES-05).
- [ ] T1.3 `servicios/lista_espera.py` (RF-RES-003, 004).
- [ ] T1.4 `servicios/clientes.py` y `servicios/bonos.py` (RF-CLI-001…007).
- [ ] T1.5 `servicios/pagos.py`, con Stripe en modo test (RF-COB-001…005).
- [ ] T1.6 `servicios/acciones_pendientes.py` (diseño §7).

## Fase 2: Agentes
- [ ] T2.1 `agentes/base.py`: contexto del turno, `@requiere_rol` y `ejecutar_subagente()` (tool runner con salida estructurada, fallbacks y caching).
- [ ] T2.2 `agentes/memoria.py`: `BetaAbstractMemoryTool` sobre la tabla `memorias`, limitada al remitente (§6.1).
- [ ] T2.3 Los cuatro subagentes, con sus prompts en `agentes/prompts/` y sus herramientas (§5).
- [ ] T2.4 `agentes/orquestador.py`: delegación, derivación y confirmaciones (§4).
- [ ] T2.5 CLI local `python -m app.chat --como +34600000000` para hablar con el agente desde la terminal, sin WhatsApp.

## Fase 3: Evals
- [ ] T3.1 Montar los evals: base de datos en memoria y fixtures por escenario, comprobando las herramientas llamadas y el estado de la base de datos.
- [ ] T3.2 Implementar todos los escenarios de `03-escenarios.md`.
- [ ] T3.3 Medir la latencia p95 y el coste por mensaje (RNF-REN-001, RNF-COS-001), y decidir el modelo de los subagentes con esos datos.

## Fase 4: WhatsApp y cron
- [ ] T4.1 Webhook con firma, deduplicación, cola y lock por número (§8, RNF-SEG-001, RNF-FIA-001).
- [ ] T4.2 Cliente de envío: texto, plantillas y descarga de medios. Transcripción de audio (RF-ORQ-006).
- [ ] T4.3 Webhook de Stripe y activación de productos (RF-COB-002).
- [ ] T4.4 Jobs del cron: recordatorios, caducidad de la lista de espera, impagos, reseñas, cumpleaños e informe semanal.
- [ ] T4.5 Resumen del historial (§6.2) y modo "en manos humanas" (RF-ORQ-004).

## Fase 5: Puesta en marcha
- [ ] T5.1 Alta en Meta Business, número nuevo y aprobación de plantillas (Q4).
- [ ] T5.2 Despliegue (Railway o Render) con HTTPS, secretos y backups.
- [ ] T5.3 Piloto con el staff durante 1 semana, y después con 10 clientes.
- [ ] T5.4 Textos legales: cláusula informativa del RGPD y consentimientos.
