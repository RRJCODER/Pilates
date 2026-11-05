# CLAUDE.md

Proyecto: un agente de IA por WhatsApp que gestiona un estudio de pilates. Lo forman un orquestador y cuatro subagentes (Reservas, Clientes, Cobros y Marketing), con memoria. Python, FastAPI, el SDK `anthropic` y PostgreSQL.

## Cómo se trabaja aquí (Spec-Driven Development)

1. Antes de tocar código, lee `specs/00-constitucion.md` y el spec de la funcionalidad.
2. No implementes nada que no esté en `specs/`. Si falta, propón primero el cambio en el spec.
3. Cita los IDs de los requisitos (`RF-RES-002`…) en los tests y en los mensajes de commit.
4. Si el código y el spec no coinciden, corrige los dos en el mismo cambio.
5. Marca las tareas completadas en `specs/04-plan-tareas.md`.

## Orden de lectura de los specs

`00-constitucion` → `01-requisitos` → `02-diseno` → `03-escenarios` → `04-plan-tareas`

## Reglas rápidas

- Los permisos se comprueban en el código (`@requiere_rol`), nunca solo en el prompt.
- La lógica de negocio va en `app/servicios/`, que se puede testear sin LLM.
- Las acciones irreversibles se hacen con `acciones_pendientes` y confirmación.
- Los modelos y el `effort` se configuran en `config/settings.py`; no se escriben a mano en el código.
