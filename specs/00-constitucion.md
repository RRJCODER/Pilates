# Constitución del proyecto

Son las reglas que no se negocian. Todos los specs, el código y los agentes de IA que trabajen en este repo tienen que cumplirlas. Si una tarea choca con alguna, primero se cambia este documento (y eso se decide a propósito) y después el código.

## 1. Spec-Driven Development (SDD)

1. **Primero el spec y después el código.** Ninguna funcionalidad se implementa sin un spec aprobado en `specs/`.
2. El flujo de cada funcionalidad es **Requisitos → Diseño → Tareas → Implementación → Verificación**.
3. Cada requisito tiene un ID (`RF-RES-001`, `RNF-SEG-002`…) y criterios de aceptación que se puedan comprobar. El código y los tests citan esos IDs.
4. Si al implementar descubrimos que el spec está mal, **se corrige el spec en el mismo PR**. El código nunca diverge del spec en silencio.
5. Un PR solo se cierra cuando todos sus criterios de aceptación tienen un test o una verificación manual documentada.

## 2. Principios del producto

1. **La base de datos es la fuente de verdad, no el modelo.** El agente nunca "recuerda" una reserva, un saldo o un pago. Siempre lo consulta con una herramienta.
2. **Las acciones irreversibles o con dinero necesitan confirmación.** Cobrar, devolver dinero, anular bonos o enviar un mensaje masivo exigen la confirmación explícita de la persona adecuada (cliente o staff, según el caso).
3. **Si hay duda, se deriva a una persona.** Lesiones, quejas, conflictos o peticiones que el agente no sabe resolver se pasan al staff. El agente no se lo inventa.
4. **Tono cercano y breve.** Es WhatsApp: mensajes cortos, de tú, sin tecnicismos y con emojis con moderación.
5. **Nada de consejos médicos.** El agente registra lesiones y avisa al instructor, pero no diagnostica ni prescribe ejercicios.

## 3. Privacidad y legal (RGPD / LOPDGDD)

1. Las lesiones y los datos de salud son **categoría especial**. Se guarda lo mínimo, solo con consentimiento explícito, y solo lo ven el staff y el subagente de Clientes.
2. Una clienta o cliente **nunca** puede ver datos de otra persona. La memoria y las consultas se filtran siempre por el `cliente_id` del remitente.
3. Para el marketing hace falta un consentimiento separado y que se pueda revocar ("BAJA" desactiva todos los envíos promocionales).
4. Las facturas se emiten mediante un software de facturación que cumpla la normativa vigente (Verifactu). El agente **no** genera facturas legales por su cuenta.

## 4. Principios técnicos

1. Python 3.12+, tipado estricto (`mypy --strict`) y `ruff`.
2. Llamamos a Claude **solo** con el SDK oficial `anthropic`. El modelo se configura por variable de entorno.
3. Cada herramienta del agente es una función pura sobre la capa de servicios. La lógica de negocio vive en `servicios/`, no en los prompts.
4. Las reglas de negocio (aforos, plazos de cancelación, caducidad de bonos) se configuran en la base de datos o en un `.toml`, nunca dentro del prompt.
5. Cada conversación se registra (mensaje, herramientas llamadas y resultado) para poder auditarla.
6. Tests: unitarios para los servicios y *evals* conversacionales para los agentes (ver `specs/04-plan-tareas.md`).
