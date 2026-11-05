# Demo pública por WhatsApp

Es un despliegue para enseñar el producto: cualquiera escribe al número de la demo y prueba el asistente como cliente, instructor o admin (RF-DEMO-*). Los pagos son simulados y los datos se borran cada noche.

## Lo que necesitas (cuentas a tu nombre)

1. **Meta for Developers**: una app de tipo *Business* con el producto WhatsApp.
2. **Hosting con Docker**: estas instrucciones son para Railway; Render o Fly.io sirven igual. Hacen falta PostgreSQL y Redis.
3. *(Opcional, para el plan IA)* una API key de Anthropic.

## 1. WhatsApp: número de prueba o número real

| | Número de prueba de Meta | Número real |
|---|---|---|
| Coste | Gratis, al momento | Un número que no esté en la app de WhatsApp |
| Quién puede probarla | **Solo hasta 5 números** que registres a mano en el panel de Meta | Cualquiera |
| Para qué sirve | Enseñarla en persona: añades el móvil del estudio antes de la reunión | Una demo pública (por ejemplo, un QR en tu web) |
| Plantillas (avisos fuera de 24 h) | Solo `hello_world` | Las de `docs/plantillas-whatsapp.md`, tras aprobarlas |

Con el número de prueba la demo funciona entera dentro de la conversación: menús, reservas, alta y pagos simulados. Lo que no llega son los avisos programados fuera de la ventana de 24 h, porque necesitan plantillas aprobadas.

Pasos en Meta:
1. developers.facebook.com → *Create app* → *Business* → añade **WhatsApp**.
2. En *WhatsApp → API Setup* copia el **Phone number ID** y genera un token. El token temporal dura 24 h; para la demo, crea un token permanente con un usuario del sistema de Business Manager.
3. Con el número de prueba, añade en *To* los móviles que vayan a probarla (máximo 5).
4. En *App settings → Basic* copia el **App secret**.
5. Después del despliegue (paso 2): en *WhatsApp → Configuration*, pon como webhook `https://TU-DOMINIO/webhook/whatsapp` y tu *verify token*, y suscríbete a **messages**.

## 2. Desplegar en Railway

1. railway.app → *New project* → *Deploy from GitHub repo* → `RRJCODER/Pilates` (la rama que quieras).
2. Añade **PostgreSQL** y **Redis** al proyecto (*New → Database*).
3. Configura el servicio del repo (**web**):
   - *Start command*: `scripts/arranque.sh web`
   - *Networking*: genera un dominio público.
   - Variables:
     ```
     DATABASE_URL=${{Postgres.DATABASE_URL}}
     REDIS_URL=${{Redis.REDIS_URL}}
     DEMO=true
     ESTUDIO_CONFIG=config/demo.toml
     CANAL=whatsapp
     PAGOS_PROVEEDOR=fake
     BASE_URL_PUBLICA=https://TU-DOMINIO
     WHATSAPP_TOKEN=…
     WHATSAPP_PHONE_NUMBER_ID=…
     WHATSAPP_APP_SECRET=…
     WHATSAPP_VERIFY_TOKEN=…   (invéntatelo)
     ANTHROPIC_API_KEY=…       (opcional: solo para el plan IA)
     ```
4. Duplica el servicio para el **worker**: mismo repo y mismas variables, sin dominio, con *Start command* `scripts/arranque.sh worker`.
5. Despliega. La web aplica las migraciones y carga el estudio demo la primera vez (30 clientes ficticios y la primera semana con reservas). El worker lo reinicia cada noche a las 04:00.
6. Comprueba que `https://TU-DOMINIO/salud` devuelve `{"estado":"ok"}` y configura el webhook en Meta (paso 1.5).

## 3. Cómo enseñarla

- Escribe **hola** al número: llega la bienvenida de la demo y el menú.
- **Darme de alta** → recibes 5 sesiones de regalo → **Reservar clase**.
- **Comprar bono** → el enlace abre `/dev/pagar/…` y simula el pago, y te llega la confirmación por WhatsApp.
- **DEMO ADMIN** → el menú de la dueña: clases de hoy, cancelar una clase, caja, informe e inactivos.
- **DEMO INSTRUCTOR** → las clases del día, asistentes con sus lesiones y marcar asistencia.
- **DEMO IA** (si hay API key) → escribe en lenguaje natural: "apúntame el lunes a reformer por la tarde".
- **DEMO BASICO** → vuelve al plan solo con menús. **DEMO** muestra la ayuda.

## Privacidad en la demo (RF-DEMO-006)

Como cualquiera puede hacerse admin, en la demo:
- los teléfonos de los clientes se ven enmascarados;
- los avisos de admin solo van al staff ficticio del TOML;
- `responder +34…` y las campañas están desactivados.

Aun así, **no la uses con datos reales**.
