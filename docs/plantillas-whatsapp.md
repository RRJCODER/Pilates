# Plantillas de WhatsApp

Fuera de la ventana de 24 h desde el último mensaje del cliente, WhatsApp solo deja enviar **plantillas aprobadas por Meta**. Dentro de esa ventana, el sistema manda el texto libre equivalente (diseño §8).

Cada estudio tiene que crear estas plantillas en *WhatsApp Manager → Plantillas de mensajes* con **el mismo nombre** y **el mismo número de variables, en el mismo orden**. El idioma es `es`. Los textos son una propuesta: se pueden adaptar al tono del estudio sin cambiar las variables.

| Nombre | Categoría | Variables | Texto propuesto |
|---|---|---|---|
| `recordatorio_clase` | Utilidad | 1 nombre · 2 clase · 3 cuándo · 4 hora · 5 instructor | Hola {{1}} 👋 Te recordamos tu clase de {{2}} {{3}} a las {{4}} con {{5}}. Si no puedes venir, respóndenos y liberamos la plaza. |
| `plaza_liberada` | Utilidad | 1 clase · 2 día · 3 hora | ¡Se ha liberado una plaza! 🎉 {{1}} el {{2}} a las {{3}}. ¿La quieres? Responde SÍ. |
| `pago_pendiente` | Utilidad | 1 nombre · 2 producto · 3 importe · 4 enlace | Hola {{1}}, tienes pendiente el pago de {{2}} ({{3}} €). Puedes pagarlo aquí: {{4}} |
| `pago_confirmado` | Utilidad | 1 producto · 2 caducidad | ✅ ¡Pago recibido! Tu {{1}} ya está activo hasta el {{2}}. ¿Te reservo alguna clase? |
| `clase_cancelada` | Utilidad | 1 clase · 2 día · 3 hora · 4 motivo | Lo sentimos 🙏 la clase de {{1}} del {{2}} a las {{3}} se ha cancelado ({{4}}). Te hemos devuelto la sesión. ¿Te busco otra clase? |
| `cambio_clase` | Utilidad | 1 clase · 2 día · 3 hora · 4 instructor | Cambio en tu clase de {{1}}: ahora es el {{2}} a las {{3}} con {{4}}. |
| `resena` | Marketing | 1 nombre · 2 enlace | ¡Gracias por venir estas semanas, {{1}}! 💛 ¿Nos dejas una reseña? Nos ayuda muchísimo: {{2}} |
| `cumpleanos` | Marketing | 1 nombre | 🎂 ¡Feliz cumpleaños, {{1}}! Todo el equipo te desea un día genial. |
| `campana_generica` | Marketing | 1 nombre · 2 texto | Hola {{1}} 👋 {{2}} (Responde BAJA para no recibir más promociones) |
| `informe_semanal` | Utilidad | 1 informe | {{1}} |
| `codigo_acceso` | Autenticación | 1 código | {{1}} es tu código para entrar en el panel del estudio. Caduca en 10 minutos. No lo compartas. |

Notas:
- Las plantillas de **marketing** solo se envían a quien tiene `consent_marketing` (RF-MKT-005).
- Si una plantilla no existe o no está aprobada, Meta devuelve un error. El worker lo registra en el log y sigue con el resto de envíos.
