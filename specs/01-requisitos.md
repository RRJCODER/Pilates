# Requisitos: agente de IA para un estudio de pilates

## 1. Visión

Es un asistente por **WhatsApp** que lleva el día a día de un estudio de pilates (reformer y suelo). Los clientes reservan, cancelan, consultan su bono y pagan escribiendo por WhatsApp. El staff gestiona el estudio con el mismo canal. Un **agente orquestador** entiende cada mensaje y lo delega en **cuatro subagentes especialistas**. Hay **memoria** por cliente para que el trato sea personal y no haya que repetir las cosas.

## 2. Actores

| Actor | Cómo se identifica | Qué puede hacer |
|---|---|---|
| **Cliente** | Su número de WhatsApp está en `clientes` | Ver su información, reservar, cancelar y pagar sus cosas |
| **Lead** | Su número no se conoce | Pedir información, hacer una clase de prueba y darse de alta |
| **Instructor/a** | Su número está en `staff` con rol `instructor` | Ver sus clases y asistentes, y marcar asistencia |
| **Admin (dueña o dueño)** | Su número está en `staff` con rol `admin` | Todo lo anterior, más configurar, cobrar, devolver, lanzar campañas y ver informes |
| **Sistema (cron)** | — | Recordatorios, avisos de caducidad e informes programados |

## 3. Requisitos funcionales

Formato EARS: *Cuando [evento], el sistema deberá [respuesta]*.

### 3.1 Orquestador (ORQ)

- **RF-ORQ-001**: Cuando llegue un mensaje, el sistema deberá identificar al remitente (cliente, lead o staff) antes de llamar al modelo.
- **RF-ORQ-002**: El orquestador deberá delegar en el subagente que corresponda según la intención, y podrá encadenar varios. Ejemplo: "reserva el jueves y dime cuántas clases me quedan" pasa por Reservas y después por Clientes.
- **RF-ORQ-003**: Cuando la intención no esté clara, el orquestador deberá hacer **una sola** pregunta para aclararla.
- **RF-ORQ-004**: Cuando haya una queja, una urgencia, una consulta médica o se pida "hablar con una persona", el sistema deberá avisar al admin y decírselo al cliente. Como el número lo usa la API, el admin contesta desde su propio WhatsApp escribiendo al asistente `responder +34… mensaje`, y devuelve el control al bot con `reactivar +34…`.
- **RF-ORQ-005**: El sistema deberá responder en el idioma del cliente (por defecto, español).
- **RF-ORQ-006**: El sistema deberá aceptar notas de voz (transcritas) e imágenes (por ejemplo, un justificante de pago).

### 3.2 Subagente de Reservas y clases (RES)

- **RF-RES-001**: Deberá mostrar las clases disponibles por día, hora, tipo (reformer o suelo), nivel e instructor, con las plazas libres.
- **RF-RES-002**: Cuando un cliente reserve, deberá comprobar que tiene un bono o mensualidad válidos y plazas suficientes, y descontar una sesión **en una transacción atómica**.
- **RF-RES-003**: Cuando la clase esté llena, deberá ofrecer la lista de espera o clases alternativas parecidas.
- **RF-RES-004**: Cuando se libere una plaza, deberá ofrecerla por orden al primero de la lista de espera, que tendrá un plazo configurable para aceptar (por defecto, 2 h).
- **RF-RES-005**: Cuando se cancele con más antelación del límite configurado (por defecto, 12 h), deberá devolver la sesión al bono. Si se cancela más tarde, deberá aplicar la política configurada (perder la sesión) y explicarla.
- **RF-RES-006**: Deberá permitir reservas recurrentes ("todos los martes a las 19:00").
- **RF-RES-007** (staff): Deberá permitir crear, modificar y cancelar clases. Si se cancela una clase, deberá avisar a todos los inscritos y devolverles la sesión.
- **RF-RES-008** (instructor): Deberá permitir consultar quién viene a sus clases y marcar asistencia o ausencia.

### 3.3 Subagente de Clientes y bonos (CLI)

- **RF-CLI-001**: Deberá dar de alta a un lead (nombre, email, nivel, objetivos, cómo nos conoció y consentimientos).
- **RF-CLI-002**: Deberá informar del bono o mensualidad activos: sesiones restantes, caducidad e historial.
- **RF-CLI-003**: Deberá vender y asignar bonos y mensualidades según el catálogo configurado, coordinándose con Cobros para el pago.
- **RF-CLI-004**: Deberá registrar lesiones y limitaciones (con consentimiento) y mostrárselas al instructor de cada clase reservada.
- **RF-CLI-005**: Deberá guardar y usar las preferencias de cada cliente en la memoria (horario habitual, instructor favorito, nivel, forma de hablar).
- **RF-CLI-006** (staff): Deberá permitir buscar clientes, ver su ficha y añadir notas internas.
- **RF-CLI-007**: Deberá permitir congelar un bono por un motivo justificado, si la política lo admite.

### 3.4 Subagente de Cobros y facturación (COB)

- **RF-COB-001**: Deberá generar un **enlace de pago** (proveedor por confirmar: Stripe o Redsys/Bizum) para bonos, mensualidades y clases sueltas.
- **RF-COB-002**: Cuando el proveedor confirme el pago (webhook), deberá activar el producto y enviar la confirmación al cliente. El modelo **nunca** marca un pago como hecho.
- **RF-COB-003**: Deberá enviar recordatorios de las mensualidades pendientes: el día del vencimiento, a los +3 días y a los +7 días, y después avisar al admin.
- **RF-COB-004**: Deberá enviar la factura que genere el software de facturación cuando el cliente la pida o tras cada pago, si así está configurado.
- **RF-COB-005** (admin): Deberá permitir registrar pagos en efectivo o por transferencia, hacer devoluciones (con confirmación explícita) y consultar la caja del día, la semana y el mes.

### 3.5 Subagente de Marketing y comunicación (MKT)

- **RF-MKT-001**: Deberá enviar recordatorios de clase (por defecto, 24 h y 2 h antes) con plantillas de WhatsApp aprobadas.
- **RF-MKT-002**: Cuando un cliente lleve más de N días sin venir (configurable; por defecto, 21), deberá proponer al admin un mensaje para recuperarlo.
- **RF-MKT-003**: Después de la 3.ª clase de un cliente nuevo, deberá pedirle una reseña en Google (una sola vez).
- **RF-MKT-004** (admin): Deberá redactar campañas (promociones, talleres, festivos) segmentadas por tipo de cliente. El admin **aprueba** cada campaña antes de enviarla.
- **RF-MKT-005**: Solo deberá enviar mensajes promocionales a quien tenga el consentimiento activo, y deberá respetar el "BAJA".
- **RF-MKT-006**: Deberá felicitar los cumpleaños, si el cliente lo consintió.
- **RF-MKT-007** (admin): Deberá enviar un informe semanal con la ocupación por franja, las altas, las bajas, los ingresos y los clientes en riesgo.

### 3.6 Memoria (MEM)

- **RF-MEM-001**: Cada cliente deberá tener una **memoria a largo plazo** con preferencias y hechos relevantes que el agente lea al empezar cada conversación.
- **RF-MEM-002**: El agente deberá poder escribir y actualizar esa memoria cuando aprenda algo útil. Las notas se guardan con fecha.
- **RF-MEM-003**: Deberá conservarse el **historial de conversación** reciente de cada número; lo antiguo se resume para que el contexto no crezca sin control.
- **RF-MEM-004**: El estudio tendrá una **memoria global** (FAQ, normas, precios, ubicación y políticas) que mantiene el admin.
- **RF-MEM-005**: Un cliente deberá poder pedir que se borren sus datos y su memoria (derecho de supresión).

### 3.7 Planes y modo menús (PLAN, BAS)

El producto se vende en dos planes. Las reglas de negocio (§3.2–3.5) son las mismas en ambos; cambia cómo se habla con el sistema.

| | Plan **Básico** | Plan **IA** |
|---|---|---|
| Menús y botones de WhatsApp | ✅ | ✅ |
| Texto libre ("cámbiame lo del martes al jueves") | ❌ se responde con el menú | ✅ lo entiende la IA |
| Memoria de preferencias, campañas redactadas, varias peticiones en un mensaje | ❌ | ✅ |
| Recordatorios, lista de espera, impagos, cumpleaños, informe semanal (cron) | ✅ | ✅ |
| Coste de IA por mensaje | 0 | Solo cuando se escribe texto libre |

- **RF-PLAN-001**: El plan de cada estudio se configura en `estudio.toml` (`[estudio] plan = "basico" | "ia"`).
- **RF-PLAN-002**: En el plan IA, las pulsaciones de botones y listas las resuelve el motor de menús **sin llamar a la IA**. Solo el texto libre pasa al orquestador.
- **RF-PLAN-003**: En los dos planes, escribir `MENÚ` (o `MENU`) muestra el menú principal.
- **RF-PLAN-004**: Si un estudio está en el plan IA pero no hay `ANTHROPIC_API_KEY`, funciona como Básico en vez de fallar.
- **RF-BAS-001**: El menú principal se adapta al rol:
  - **Contacto nuevo**: información, precios, clase de prueba/alta y hablar con el estudio.
  - **Cliente**: reservar, mis reservas (cancelar), mi bono, comprar bono y hablar con el estudio.
  - **Instructor**: mis clases de hoy y mañana (asistentes y asistencia).
  - **Admin**: lo del instructor, más cancelar una clase, la caja de hoy, el informe semanal y los clientes inactivos.
- **RF-BAS-002**: Reservar se hace por pasos: día (próximos 7 días con plazas) → clase → reserva. Si la clase está llena, se ofrece apuntarse a la lista de espera.
- **RF-BAS-003**: Antes de cancelar se muestra la política (si se devuelve la sesión o no) y se pide confirmar con un botón.
- **RF-BAS-004**: El alta de un contacto nuevo pide el nombre por texto y después dos botones: privacidad (obligatoria) y promociones (opcional). Los datos de salud no se piden en el plan Básico.
- **RF-BAS-005**: Palabras clave en el plan Básico: `BAJA` (promociones), `SÍ` (acepta una plaza ofrecida o confirma la acción pendiente) y `NO`. Cualquier otro texto libre muestra el menú con un aviso breve.
- **RF-BAS-006**: Las acciones con confirmación (diseño §7) se confirman con botones `✅ Confirmar` / `❌ Cancelar`.
- **RF-BAS-007**: En el plan Básico, los pagos manuales, las devoluciones y las campañas no están disponibles por WhatsApp (en v2 irán en el panel web). Pedirlos deriva al admin. *Propuesta: pasan al panel web (RF-PAN-006/007).*

### 3.8 Modo demo (DEMO)

Es un despliegue de prueba para enseñar el producto a estudios desde el móvil. **Nunca se activa en un estudio real**: hace falta `DEMO=true` en las variables de entorno.

- **RF-DEMO-001**: Al darse de alta en la demo, el contacto recibe un **bono demo de 5 sesiones** gratis, para que pueda reservar enseguida. Los pagos siempre son simulados (`PAGOS_PROVEEDOR=fake`).
- **RF-DEMO-002**: Los comandos `DEMO ADMIN`, `DEMO INSTRUCTOR` y `DEMO CLIENTE` cambian el rol de quien escribe, para que pueda ver cada menú. `DEMO BASICO` y `DEMO IA` cambian el plan del estudio demo (`DEMO IA` solo si hay `ANTHROPIC_API_KEY`). `DEMO` a secas explica los comandos.
- **RF-DEMO-003**: El estudio demo tiene clientes ficticios con reservas, para que haya ocupación realista y alguna clase completa (para probar la lista de espera).
- **RF-DEMO-004**: Cada noche (04:00) se borran todos los datos y se vuelve a cargar el estudio demo.
- **RF-DEMO-005**: El primer mensaje de un contacto nuevo en la demo explica que es una demostración y cómo usar los comandos `DEMO`.
- **RF-DEMO-006** (privacidad): en la demo, cualquiera puede hacerse admin, así que:
  - los teléfonos de los clientes se muestran enmascarados (`+34 6•• ••• 123`);
  - los avisos para el admin (altas, derivaciones, impagos, informe) solo van al staff original del TOML, nunca a visitantes;
  - `responder +34…` y el envío de campañas están desactivados, para que nadie pueda escribir a otros visitantes desde el número de la demo.

### 3.9 Panel web del estudio (PAN)

> **Estado: propuesta pendiente de aprobar.** Resuelve la decisión Q3. Hasta que se apruebe, no se implementa nada de esta sección.

Es una web para el **staff** (admin e instructores) que complementa a WhatsApp: lo que en un chat es incómodo (ver la semana entera, buscar una clienta, cuadrar la caja, contestar conversaciones derivadas) se hace en una pantalla. Los clientes y los leads siguen usando solo WhatsApp. Funciona en el móvil y en el ordenador, y **no llama a la IA** (coste 0 en los dos planes).

- **RF-PAN-001** (acceso): El staff entra en `/panel` escribiendo su teléfono. Si el número está en `staff` y activo, el sistema le envía por WhatsApp un **código de 6 cifras** que caduca a los 10 minutos. Con el código correcto se abre una sesión de 12 h (configurable). Si el número no es de staff, la pantalla responde lo mismo que si lo fuera, para no revelar quién trabaja en el estudio.
- **RF-PAN-002** (agenda): Deberá mostrar la agenda por semanas con cada clase, su instructor, la ocupación (`7/10`) y la lista de espera. El admin ve todas las clases; un instructor, solo las suyas.
- **RF-PAN-003** (clase): Al abrir una clase se ven los asistentes, sus lesiones activas (solo si dieron el consentimiento de salud) y botones para marcar asistencia o falta (RF-RES-008).
- **RF-PAN-004** (admin, clases): Deberá permitir crear, mover y cancelar clases (RF-RES-007). Cancelar pide confirmación en pantalla y avisa a las personas apuntadas igual que si se hiciera por WhatsApp.
- **RF-PAN-005** (clientes): Deberá tener un buscador de clientes y su ficha: bono y sesiones, próximas reservas, historial de asistencia, pagos y notas internas (RF-CLI-006). El staff puede añadir notas internas. La ficha muestra lo mismo que `ficha_cliente`, sin datos de salud si no hay consentimiento.
- **RF-PAN-006** (admin, cobros): Deberá mostrar la caja del día, la semana y el mes, y permitir registrar pagos en efectivo o por transferencia y hacer devoluciones (RF-COB-005). Las dos acciones piden confirmación (diseño §7). Está disponible **también en el plan Básico** (cierra RF-BAS-007).
- **RF-PAN-007** (admin, campañas): Deberá permitir elegir un segmento (nivel, inactividad, tipo de bono), ver cuántas personas con consentimiento de marketing lo forman, escribir el texto y enviarlo tras confirmarlo (RF-MKT-004/005). En el plan IA hay un botón opcional "Proponer texto" que usa el subagente de Marketing.
- **RF-PAN-008** (admin, conversaciones): Deberá listar las conversaciones derivadas a una persona (RF-ORQ-004), mostrar su historial, permitir contestar desde el panel (equivale a `responder +34…`) y devolver el control al bot (equivale a `reactivar +34…`).
- **RF-PAN-009** (admin, informes): Deberá mostrar el informe semanal (RF-MKT-007) y la lista de clientes inactivos (RF-MKT-002).
- **RF-PAN-010** (permisos): El panel usa **los mismos servicios** que los agentes y el motor de menús, con un `Contexto` construido a partir del staff que ha iniciado sesión. Los permisos los sigue comprobando `@requiere_rol` (RNF-SEG-002); ocultar un botón no basta.
- **RF-PAN-011** (demo): Con `DEMO=true`, el panel cumple RF-DEMO-006: teléfonos enmascarados, y contestar conversaciones y enviar campañas desactivados.

## 4. Requisitos no funcionales

- **RNF-REN-001**: Tiempo de respuesta p95 inferior a 15 s por mensaje (WhatsApp tolera latencia, pero no silencios largos).
- **RNF-REN-002**: Debe soportar estudios de hasta 500 clientes activos y 80 clases semanales con un solo despliegue.
- **RNF-SEG-001**: El webhook de WhatsApp debe verificar la firma `X-Hub-Signature-256`.
- **RNF-SEG-002**: Las herramientas comprueban el rol del remitente en el **código**, no en el prompt. Un cliente no puede ejecutar herramientas de staff aunque se lo pida al modelo.
- **RNF-SEG-003**: Los secretos (API keys y tokens) van en variables de entorno, nunca en el repo.
- **RNF-SEG-004** (panel, propuesta): Las sesiones del panel usan una cookie `HttpOnly`, `Secure` y `SameSite=Strict`, y los formularios llevan token CSRF. Como mucho 5 intentos por código y 3 códigos por teléfono cada 15 minutos. Los códigos y los tokens de sesión se guardan con hash, nunca en claro.
- **RNF-SEG-005** (panel, propuesta): Cada acción del panel queda en la auditoría (`eventos_agente` con `agente = "panel"`), con quién la hizo.
- **RNF-FIA-001**: Los mensajes de WhatsApp se procesan de forma idempotente (por `message_id`), porque Meta reintenta los envíos.
- **RNF-FIA-002**: Si Claude o la base de datos fallan, el cliente recibe un mensaje de "ahora mismo no puedo, te escribe alguien del estudio" y se avisa al admin.
- **RNF-OBS-001**: Cada turno registra los tokens usados, el coste estimado, las herramientas llamadas y la latencia.
- **RNF-COS-001**: Coste objetivo por debajo de 0,02 € por mensaje de cliente, de media.

## 5. Fuera de alcance (v1)

- Una app móvil propia (WhatsApp es el canal).
- Videoclases o contenido on-demand.
- Generar facturas legales internamente (se integra un software homologado).
- Varios estudios en un mismo despliegue (el esquema ya lleva `estudio_id`; se implementará en v2).

## 6. Producto genérico (decisiones)

Todavía no hay un estudio concreto como cliente, así que el producto es **genérico y configurable**. Se adapta a cualquier estudio de pilates sin tocar el código.

- **RF-GEN-001**: Todo lo que es propio de un estudio (nombre, tono, horarios, tipos de clase, aforos, catálogo de bonos, precios, políticas de cancelación y lista de espera, recordatorios, FAQ) se define en `config/estudio.toml` y se carga en la base de datos con `seed`.
- **RF-GEN-002**: Los pagos, la facturación, la mensajería y la transcripción de audio se integran mediante **adaptadores** con una interfaz común. Cada uno tiene una implementación `fake` para desarrollo y evals.
- **RF-GEN-003**: Todas las tablas de negocio llevan `estudio_id`. En v1 hay **un estudio por despliegue** (marca blanca), pero el esquema queda listo para un SaaS con varios estudios en v2.
- **RF-GEN-004**: El canal es un adaptador: `whatsapp` (Meta Cloud API) en producción y `consola` para pruebas locales. Los dos soportan texto, plantillas, botones (máx. 3) y listas (máx. 10 opciones).

| # | Decisión pendiente para cada estudio | Solución genérica en v1 |
|---|---|---|
| Q1 | Proveedor de pagos | `PagosAdapter`: `fake` y `stripe` (Redsys/Bizum como adaptador futuro) |
| Q2 | Software de facturación | `FacturacionAdapter`: `ninguno` y `holded` (otros como adaptador) |
| Q3 | Panel web para el admin | Solo WhatsApp y consola en v1. **Propuesta:** panel web para el staff (§3.9, RF-PAN-*) |
| Q4 | Número de WhatsApp | Cada estudio da de alta su propio número en Meta (guía en `docs/`) |
| Q5 | Catálogo, precios y políticas | Estudio de ejemplo en `config/estudio.toml` |
| Q6 | Google Calendar | No en v1 |
