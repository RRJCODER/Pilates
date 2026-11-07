# Diseño técnico

> Implementa los requisitos de `01-requisitos.md` y respeta `00-constitucion.md`.

## 1. Vista general

```
 WhatsApp (cliente / staff)
        │  webhook (Meta Cloud API)
        ▼
 ┌──────────────────────────────┐
 │ FastAPI  /webhook/whatsapp   │  verificar firma · deduplicar message_id
 └──────────────┬───────────────┘
                ▼  cola (arq + Redis): responder rápido a Meta y procesar aparte
 ┌──────────────────────────────┐
 │ Manejador de conversación    │  1. identificar remitente y rol (código)
 │                              │  2. cargar memoria e historial
 │                              │  3. ejecutar el ORQUESTADOR
 │                              │  4. enviar la respuesta y guardar el turno
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────────────────────────────────┐
 │ ORQUESTADOR (Claude + tool runner)                        │
 │  herramientas: delegar_reservas · delegar_clientes ·      │
 │                delegar_cobros · delegar_marketing ·       │
 │                derivar_a_humano · confirmar_accion ·      │
 │                memory (memoria del cliente / estudio)     │
 └───┬───────────────┬───────────────┬───────────────┬───────┘
     ▼               ▼               ▼               ▼
  RESERVAS        CLIENTES        COBROS         MARKETING      ← subagentes: cada uno con
  (tool runner)   (tool runner)   (tool runner)  (tool runner)     su prompt y sus herramientas
     │               │               │               │
     └───────────────┴──── servicios/ (lógica de negocio) ───┘
                              │
                     PostgreSQL (Supabase) · Stripe · Holded
 Cron (arq): recordatorios, lista de espera, impagos, informes → subagentes
```

## 2. Stack

| Pieza | Elección | Motivo |
|---|---|---|
| Lenguaje | Python 3.12 | Lo eligió el cliente; SDK oficial `anthropic` |
| API web | FastAPI + Uvicorn | Webhooks asíncronos y validación con Pydantic |
| Cola | arq + Redis | Responder 200 a Meta al momento y procesar en segundo plano |
| Base de datos | PostgreSQL (Supabase) + SQLAlchemy 2 + Alembic | Transacciones para aforos, backups y panel gratuito |
| LLM | Claude a través de `anthropic` (Tool Runner, `client.beta.messages.tool_runner`) | Bucle de agente sin tener que escribirlo a mano y con herramientas tipadas |
| Canal | `CanalAdapter`: `whatsapp` (Meta Cloud API) · `consola` | Sin intermediarios; plantillas para los mensajes proactivos |
| Pagos | `PagosAdapter`: `fake` · `stripe` (Payment Links + webhook) | Enlace en el chat y confirmación verificada |
| Facturación | `FacturacionAdapter`: `ninguno` · `holded` | Cumplimiento de Verifactu delegado |
| Transcripción | `STTAdapter`: `fake` · por decidir | Notas de voz |

**Adaptadores (RF-GEN-002).** Viven en `app/integraciones/<tipo>/` y siguen un `Protocol` común. Cada estudio elige su implementación con variables de entorno (`PAGOS_PROVEEDOR=stripe`…). Las implementaciones `fake` permiten desarrollar y lanzar los evals sin cuentas externas.
| Programador | `arq` cron (en el worker) | Recordatorios e informes, sin otra dependencia |
| Despliegue | Docker en Railway/Render/Fly | Barato, con HTTPS para el webhook |

### Por qué el Tool Runner del SDK `anthropic` y no el Claude Agent SDK

El Claude Agent SDK es Claude Code empaquetado: incluye un sistema de archivos, bash y herramientas de programación. Aquí no hace falta nada de eso. Este agente solo necesita **nuestras** herramientas de negocio (reservar, cobrar…), comprobando los permisos en el código. Con el Tool Runner, cada subagente es un `tool_runner` con su propio *system prompt* y su propio subconjunto de herramientas. Es más ligero, más barato y más fácil de controlar.

## 3. Modelos y parámetros

Todo se configura en `config/settings.py` mediante variables de entorno.

| Agente | Modelo por defecto | `effort` | Motivo |
|---|---|---|---|
| Orquestador | `claude-opus-5-5` | `medium` | Entiende la intención, encadena subagentes y redacta el tono final |
| Subagentes | `claude-opus-5-5` | `low` | Tareas acotadas con herramientas; menos latencia y menos coste |

- `thinking`: se omite. En Opus 5.5 es adaptativo y no se puede desactivar; la profundidad se controla con `effort`.
- **Fallback ante rechazos**: `fallbacks="default"` con la beta `server-side-fallback-2026-07-01`, y se comprueba siempre `stop_reason == "refusal"`.
- **Prompt caching**: las herramientas y el *system prompt* son estables (incluida la memoria global del estudio) y llevan `cache_control`. Lo variable (fecha y hora, ficha del cliente) va **después** del punto de caché.
- `tool_choice`: siempre `auto`. En Opus 5.5 no se puede forzar `any` ni `tool`. Los esquemas de las herramientas se generan desde la firma de cada función (`@beta_tool`) con `additionalProperties: false`, y el servicio valida otra vez los datos.
- **Message threads**: el Tool Runner del SDK continúa cada bucle con *message threads* (solo envía los mensajes nuevos). Esta función no es apta para ZDR; si un estudio exigiera ZDR, habría que desactivarla.
- Si al medir costes en la fase de evals resulta caro, se prueba a cambiar los subagentes a `claude-sonnet-5-5` o `claude-haiku-5-5` (decisión del cliente, con datos).

## 4. Orquestador

### 4.1 Contexto que recibe en cada turno

1. **System prompt estable** (cacheado): identidad, tono, normas y la memoria global del estudio (FAQ, precios, políticas).
2. **System prompt variable**: fecha y hora actuales (Europe/Madrid), rol del remitente y resumen de su ficha (nombre, bono, próximas reservas).
3. **Historial**: el resumen de la conversación antigua más los últimos 20 mensajes.
4. **Mensaje nuevo**: texto, transcripción del audio o imagen.

### 4.2 Herramientas del orquestador

| Herramienta | Descripción |
|---|---|
| `delegar_reservas(tarea: str)` | Ejecuta el subagente de Reservas y devuelve un resumen estructurado |
| `delegar_clientes(tarea: str)` | Ídem con Clientes y bonos |
| `delegar_cobros(tarea: str)` | Ídem con Cobros |
| `delegar_marketing(tarea: str)` | Ídem con Marketing (solo para el staff, salvo "BAJA" y consultas de consentimiento) |
| `derivar_a_humano(motivo, urgencia)` | Avisa al admin por WhatsApp y marca la conversación como "en manos humanas" |
| `confirmar_accion(codigo)` / `rechazar_accion(codigo)` | Ejecuta o descarta una acción pendiente (ver §7) |
| `reactivar_asistente(telefono)` (solo admin) | Devuelve el control al bot en una conversación derivada |
| `memory` (`memory_20250818`) | Escribe y consulta la memoria a largo plazo (ver §6) |

**El código, no el modelo, inyecta el contexto del remitente.** `delegar_*` recibe solo `tarea`. El `cliente_id` y el `rol` se pasan al subagente desde el contexto del turno. Así el modelo no puede actuar en nombre de otra persona (RNF-SEG-002).

### 4.3 Salida del subagente

Cada subagente termina con un **resumen en texto** (qué hizo, el resultado y los ids). Es el **código**, no el modelo, quien añade las acciones pendientes que se crearon durante su ejecución:

```json
{ "resumen": "Reservada clase Reformer jueves 19:00 con Laura (reserva_id 81). Quedan 5 sesiones.",
  "acciones_pendientes": [ { "codigo": "A7K2", "descripcion": "...", "expira_en_minutos": 15 } ] }
```

Así el código de confirmación no depende de que el modelo lo copie bien. El orquestador redacta el mensaje final para WhatsApp a partir de esta salida.

### 4.4 Herramientas visibles según el rol

A cada subagente solo se le ofrecen las herramientas del rol de quien escribe: `alta_cliente` a los contactos nuevos, las de staff a instructores y admin, y las de admin solo al admin. Es defensa en profundidad, porque el servicio vuelve a comprobar el rol con `@requiere_rol`.

## 5. Subagentes y herramientas

En cada herramienta, el **rol mínimo** se comprueba en el código con el decorador `@requiere_rol(...)`. ✋ = la acción crea una acción pendiente de confirmación.

### 5.1 Reservas y clases

| Herramienta | Rol | Requisito |
|---|---|---|
| `buscar_clases(desde, hasta, tipo?, nivel?, instructor?)` | lead | RF-RES-001 |
| `reservar_clase(clase_id)` | cliente | RF-RES-002 |
| `reservar_recurrente(dia_semana, hora, tipo, semanas)` | cliente | RF-RES-006 |
| `cancelar_reserva(reserva_id)` | cliente | RF-RES-005 |
| `unirse_lista_espera(clase_id)` | cliente | RF-RES-003 |
| `aceptar_plaza_lista_espera(clase_id)` | cliente | RF-RES-004 |
| `clases_del_instructor(desde, hasta)` | instructor | RF-RES-008 |
| `mis_reservas()` | cliente | RF-RES-001 |
| `asistentes_clase(clase_id)` | instructor | RF-RES-008 |
| `marcar_asistencia(reserva_id, estado)` | instructor | RF-RES-008 |
| `crear_clase(...)` / `modificar_clase(...)` | admin | RF-RES-007 |
| `cancelar_clase(clase_id, motivo)` ✋ | admin | RF-RES-007 |

### 5.2 Clientes y bonos

| Herramienta | Rol | Requisito |
|---|---|---|
| `alta_cliente(nombre, email, nivel, objetivos, consentimientos)` | lead | RF-CLI-001 |
| `estado_bono()` / `mi_ficha()` | cliente | RF-CLI-002 |
| `actualizar_consentimientos(salud?, marketing?)` | cliente | Constitución §3 |
| `catalogo_bonos()` | lead | RF-CLI-003 |
| `registrar_lesion(descripcion)` (requiere consentimiento de salud) / `lesion_resuelta()` | cliente | RF-CLI-004 |
| `congelar_bono(motivo, hasta)` ✋ | cliente | RF-CLI-007 |
| `buscar_cliente(texto)` / `ficha_cliente(id)` | instructor | RF-CLI-006 |
| `nota_interna(cliente_id, texto)` (tabla `notas_internas`; el cliente nunca la ve) | instructor | RF-CLI-006 |
| `borrar_mis_datos()` ✋ | cliente | RF-MEM-005 |

### 5.3 Cobros y facturación

| Herramienta | Rol | Requisito |
|---|---|---|
| `crear_enlace_pago(producto_id)` | lead | RF-COB-001 |
| `pagos_pendientes()` | cliente | RF-COB-003 |
| `enviar_factura(pago_id)` | cliente | RF-COB-004 |
| `registrar_pago_manual(cliente_id, importe, metodo, producto_id)` ✋ | admin | RF-COB-005 |
| `devolver_pago(pago_id, motivo)` ✋ | admin | RF-COB-005 |
| `resumen_caja(desde, hasta)` | admin | RF-COB-005 |

El pago **solo** se da por confirmado en `POST /webhook/stripe` (firma verificada). Ninguna herramienta marca un pago como pagado (RF-COB-002).

### 5.4 Marketing y comunicación

| Herramienta | Rol | Requisito |
|---|---|---|
| `darse_de_baja_promos()` | cliente | RF-MKT-005 |
| `clientes_inactivos(dias)` | admin | RF-MKT-002 |
| `segmentar(filtro)` | admin | RF-MKT-004 |
| `borrador_campana(segmento, objetivo)` | admin | RF-MKT-004 |
| `enviar_campana(campana_id)` ✋ | admin | RF-MKT-004 |
| `informe_semanal()` | admin | RF-MKT-007 |

Los recordatorios (RF-MKT-001), la reseña (RF-MKT-003) y los cumpleaños (RF-MKT-006) los lanza **el cron directamente** con plantillas, sin pasar por el LLM. Así salen baratos y deterministas.

## 6. Memoria

Hay tres capas, de la más estructurada a la más libre:

| Capa | Dónde | Qué guarda | Cómo la usa el agente |
|---|---|---|---|
| **Datos de negocio** | Tablas de PostgreSQL | Reservas, bonos, pagos, lesiones | Herramientas de los subagentes (fuente de verdad) |
| **Memoria a largo plazo** | Tabla `memorias` expuesta como herramienta `memory` (`memory_20250818`) | Preferencias, hechos y contexto personal | El agente lee y escribe archivos virtuales |
| **Memoria de conversación** | Tablas `mensajes` y `resumenes` | Últimos 20 mensajes + resumen de lo anterior | El código la inyecta en cada turno |

### 6.1 Herramienta `memory` con backend en PostgreSQL

Se implementa con `BetaAbstractMemoryTool` del SDK, con una ruta virtual **limitada por el código** al remitente:

```
/memories/
  ├── cliente/            → filas de `memorias` WHERE cliente_id = <remitente>  (lectura y escritura)
  │     ├── preferencias.md
  │     └── notas.md
  └── estudio/            → memoria global: FAQ, normas, precios
                            (lectura para todos; escritura solo con rol admin)
```

- Un cliente **no puede** abrir la memoria de otro: la ruta `/memories/cliente/` siempre apunta a la suya y no hay forma de escribir otro id.
- Los datos de salud **no** van a la memoria libre. Van en la tabla `lesiones` (con consentimiento) y se consultan con herramientas.
- Para ahorrar una llamada por turno, el código **inyecta** el contenido de `/memories/cliente/` en la parte variable del *system prompt* y el de `/memories/estudio/` en la parte estable (cacheada). La herramienta se usa sobre todo para **escribir**: el prompt le pide al orquestador que guarde lo útil (por ejemplo, "prefiere a Laura" o "trabaja a turnos y cambia de horario cada semana").
- Cada archivo tiene un máximo de 4000 caracteres.

### 6.2 Resumen de la conversación

Cuando un número supera los 40 mensajes sin resumir, el worker llama a Claude (`effort: low`) para resumir los más antiguos en `resumenes` y los marca como resumidos. El historial que se envía al modelo queda en *resumen + últimos 20*.

## 7. Acciones que requieren confirmación

1. Una herramienta marcada con ✋ **no ejecuta nada**: crea una fila en `acciones_pendientes` (`codigo`, `tipo`, `payload`, `quien_confirma`, `expira_en` = 15 min) y devuelve `requiere_confirmacion`.
2. El orquestador pregunta: *"¿Confirmas la devolución de 60 € a Marta? Responde SÍ"*.
3. Si la persona responde que sí, el orquestador llama a `confirmar_accion(codigo)`. **El código comprueba** que el remitente es `quien_confirma` y que la acción no ha caducado, y entonces la ejecuta.

## 8. WhatsApp

- **Recepción**: `GET /webhook/whatsapp` (verificación del token) y `POST /webhook/whatsapp` (mensajes). Se verifica `X-Hub-Signature-256`, se deduplica por `message_id`, se encola y se responde 200.
- **Mensajes de voz**: se descarga el audio y se transcribe con el `STTAdapter` antes del orquestador. El proveedor está por decidir; con `fake`, el asistente pide que lo escriban.
- **Imágenes y documentos**: se reenvían al admin (aviso) y el orquestador recibe una nota. Si parece un justificante de pago, dice que el equipo lo comprobará y no activa nada (RF-COB-002).
- **Modo humano**: tras `derivar_a_humano`, el bot no responde a ese número durante `humano_horas` (4 h por defecto). Los mensajes se reenvían al admin, que puede devolver el control con "reactivar +34…".
- **Ventana de 24 h**: dentro de la ventana se envía texto libre. Fuera de ella **solo** se pueden enviar plantillas aprobadas por Meta (recordatorios, impagos, campañas, ofertas de lista de espera).
- **Plantillas**: ver `docs/plantillas-whatsapp.md`. Los avisos a terceros se guardan también en la conversación del destinatario, para que el bot entienda su respuesta (por ejemplo, un "SÍ" a una plaza liberada).
- **Serialización**: los mensajes de un mismo número se procesan de uno en uno (lock en Redis por número) para no cruzar las respuestas.

## 9. Modelo de datos (resumen)

> Todas las tablas, salvo `estudio`, llevan `estudio_id` (RF-GEN-003); se omite abajo para que se lea mejor. Los teléfonos son únicos **por estudio**.

```
estudio(id, nombre, zona_horaria, config_json)   ← datos, políticas y horario semanal del TOML
staff(id, nombre, telefono, rol[admin|instructor], activo)
clientes(id, nombre, telefono UNIQUE, email, nivel, objetivos, fecha_nac,
         consent_salud, consent_marketing, consent_fecha, estado, creado_en)
lesiones(id, cliente_id, descripcion, activa, creado_en)
tipos_clase(id, nombre, modalidad[reformer|suelo], nivel, duracion_min, aforo)
clases(id, tipo_id, instructor_id, inicio, fin, aforo, estado[programada|cancelada])
reservas(id, clase_id, cliente_id, estado[confirmada|cancelada|asistio|falta],
         bono_id, creada_en, cancelada_en)          UNIQUE(clase_id, cliente_id)
lista_espera(id, clase_id, cliente_id, posicion, ofrecida_en, expira_en, estado)
productos(id, nombre, tipo[bono|mensualidad|suelta], sesiones, validez_dias, precio)
bonos(id, cliente_id, producto_id, sesiones_total, sesiones_usadas,
      inicio, caduca, estado[activo|congelado|caducado], pago_id)
pagos(id, cliente_id, producto_id, importe, metodo, estado, proveedor_ref, factura_ref, creado_en)
acciones_pendientes(codigo, tipo, payload_json, quien_confirma, expira_en, estado)
memorias(id, ambito[cliente|estudio], cliente_id NULL, ruta, contenido, actualizado_en)
conversaciones(id, telefono, cliente_id NULL, estado[bot|humano], ultima_entrada_en, humano_hasta,
               flujo JSON NULL)                     ← paso de un flujo de menú que espera texto
mensajes(id, conversacion_id, wa_message_id UNIQUE, rol, contenido, resumido, creado_en)
resumenes(id, conversacion_id, contenido, hasta_mensaje_id)
campanas(id, segmento_json, texto, plantilla, estado, aprobada_por, enviada_en)
notas_internas(id, cliente_id, staff_id, texto, creado_en)
envios_programados(id, clave UNIQUE, creado_en)     ← idempotencia del cron
eventos_agente(id, conversacion_id, agente, herramienta, input, output, tokens_in,
               tokens_out, latencia_ms, creado_en)          ← auditoría (RNF-OBS-001)
```

**Concurrencia en las reservas** (RF-RES-002): `SELECT ... FOR UPDATE` sobre la clase y el bono dentro de una transacción, con el aforo comprobado en la misma transacción. Cada llamada a una herramienta es una transacción propia, así que no se mantienen bloqueos mientras el modelo piensa.

**Fechas**: se guardan siempre en UTC; se muestran en la zona horaria del estudio.

## 10. Planes y motor de menús (RF-PLAN-*, RF-BAS-*)

```
mensaje entrante
   ├─ ¿pulsación de botón/lista (respuesta_id)? ──────────► motor de menús (sin IA)
   ├─ ¿hay un flujo esperando texto (p. ej. el nombre)? ──► motor de menús
   ├─ ¿palabra clave (MENÚ, BAJA, SÍ, NO)? ───────────────► motor de menús
   └─ texto libre ─┬─ plan IA ──────► orquestador (§4)
                   └─ plan Básico ──► motor de menús (aviso + menú principal)
```

- **Sin estado en los botones**: el id de cada opción lleva la acción y sus datos (`r:dia:2026-11-05`, `r:clase:123`, `cx:45`, `ok:A7K2`…), así que pulsar un botón antiguo sigue funcionando. Los servicios vuelven a comprobar todo (aforo, propiedad de la reserva, rol).
- **Estado solo para texto**: los flujos que esperan texto (el nombre en el alta) guardan su paso en `conversaciones.flujo` (JSON). Cualquier botón o `MENÚ` lo descarta.
- **Salidas**: el motor devuelve una lista de `Texto | Botones | Lista`. El canal las envía con los límites de WhatsApp: hasta 3 botones de 20 caracteres, y listas de hasta 10 filas, con títulos de 24 caracteres y descripciones de 72. El motor recorta los textos para no superarlos.
- **Historial**: se guardan tanto la opción elegida (su título) como lo que responde el menú, así que si el estudio tiene plan IA, la IA tiene contexto cuando alguien pasa a escribir.
- El motor llama a los **mismos servicios** que los subagentes, con el mismo `Contexto` y los mismos `@requiere_rol`.

## 12. Panel web del estudio (RF-PAN-*) — propuesta

> **Estado: propuesta pendiente de aprobar.**

**Tecnología.** Páginas renderizadas en el servidor con **FastAPI + Jinja2**, y **HTMX** para las acciones pequeñas (marcar asistencia, filtrar) sin recargar la página. No hay paso de build ni framework de JavaScript, y se despliega con el mismo contenedor `web`. Dependencias nuevas: `jinja2` y `python-multipart`. HTMX se sirve como archivo estático del repo.

**Acceso (RF-PAN-001, RNF-SEG-004).**

```
/panel/entrar  ── teléfono ──► ¿staff activo? ──sí──► código de 6 cifras por WhatsApp (plantilla `codigo_acceso`)
                                   └─no──► misma pantalla, no se envía nada
/panel/codigo  ── código ──► ¿hash correcto, sin caducar, < 5 intentos? ──► cookie de sesión (12 h)
```

- Con `CANAL=consola`, el código se imprime en la terminal, para probarlo en local.
- Tablas nuevas:
  ```
  codigos_acceso(id, staff_id, codigo_hash, expira_en, intentos, usado, creado_en)
  sesiones_panel(token_hash PK, staff_id, expira_en, creado_en, ultimo_uso_en)
  ```
- Cada petición busca la sesión, comprueba que el staff sigue activo y construye un `Contexto(rol=ADMIN|INSTRUCTOR, staff_id, telefono)`. Desde ahí todo pasa por los servicios con `@requiere_rol` (RF-PAN-010).

**Páginas.**

| Ruta | Rol | Servicios que usa | Requisito |
|---|---|---|---|
| `/panel` (agenda semanal) | instructor | `buscar_clases`, `clases_de_instructor` | RF-PAN-002 |
| `/panel/clases/{id}` | instructor | `asistentes_clase`, `marcar_asistencia` | RF-PAN-003 |
| `/panel/clases/nueva`, `/panel/clases/{id}/editar` | admin | `crear_clase`, `modificar_clase`, `cancelar_clase` ✋ | RF-PAN-004 |
| `/panel/clientes`, `/panel/clientes/{id}` | instructor | `buscar_cliente`, `ficha_cliente`, `nota_interna` | RF-PAN-005 |
| `/panel/caja` | admin | `resumen_caja`, `registrar_pago_manual` ✋, `devolver_pago` ✋ | RF-PAN-006 |
| `/panel/campanas` | admin | `segmentar`, `borrador_campana`, `enviar_campana` ✋ | RF-PAN-007 |
| `/panel/conversaciones` | admin | historial y modo humano (§8) | RF-PAN-008 |
| `/panel/informes` | admin | `informe_semanal`, `clientes_inactivos` | RF-PAN-009 |

**Confirmaciones (§7).** Las acciones ✋ funcionan igual que en WhatsApp: el servicio crea la fila en `acciones_pendientes` con `quien_confirma` = teléfono del staff, y el panel muestra una pantalla de confirmación con el resumen y los botones *Confirmar* / *Cancelar*. *Confirmar* llama a `confirmar_accion(codigo)`, que vuelve a comprobar el remitente y la caducidad.

**Avisos.** Los `ctx.avisos` que generan los servicios (por ejemplo, avisar a los apuntados de una clase cancelada) se envían por el canal después del commit, con el mismo código que usa `conversacion.py`.

**Configuración** (`config/settings.py`): `PANEL_SESION_HORAS=12`, `PANEL_CODIGO_MINUTOS=10`. Se pueden cambiar sin tocar el código.

**Estructura.**

```
app/panel/
├── rutas.py        ← APIRouter montado en /panel
├── acceso.py       ← códigos, sesiones, CSRF y dependencia `staff_actual`
├── plantillas/     ← Jinja2 (base.html + una por página)
└── estaticos/      ← CSS y htmx.min.js
```

## 11. Estructura del repositorio

```
pilates/
├── specs/                  ← SDD: la fuente de verdad del diseño
├── config/
│   ├── settings.py         ← variables de entorno (pydantic-settings)
│   └── estudio.toml        ← catálogo, políticas y horarios de ejemplo
├── app/
│   ├── main.py             ← FastAPI: webhooks de WhatsApp y Stripe, /dev/pagar (fake)
│   ├── conversacion.py     ← manejador: identificar → orquestador → enviar
│   ├── worker.py           ← arq: cola de mensajes + cron
│   ├── chat.py             ← chat por consola (T2.5)
│   ├── webhooks/cola.py    ← encolado en Redis
│   ├── agentes/
│   │   ├── claude.py       ← cliente, Tool Runner, fallbacks, caching, auditoría
│   │   ├── herramientas.py ← servicio → herramienta (transacción, errores, log)
│   │   ├── subagentes.py   ← herramientas de Reservas, Clientes, Cobros y Marketing
│   │   ├── orquestador.py  ← prompts y delegación
│   │   ├── memoria.py      ← BetaAbstractMemoryTool sobre PostgreSQL
│   │   ├── resumen.py      ← resumen de conversaciones largas
│   │   └── prompts/        ← un .md por agente
│   ├── menu/               ← motor de menús y botones (plan Básico y pulsaciones del plan IA)
│   ├── panel/              ← panel web del staff (§12, propuesta)
│   ├── servicios/          ← lógica de negocio pura (testeable sin LLM)
│   ├── integraciones/      ← canal.py, pagos.py, facturacion.py, stt.py
│   └── db/                 ← modelos SQLAlchemy, sesión y seed
├── migrations/             ← Alembic
├── tests/
│   ├── unit/               ← servicios
│   └── evals/              ← conversaciones de prueba contra los agentes
├── docker-compose.yml      ← postgres + redis + app + worker
└── pyproject.toml
```
