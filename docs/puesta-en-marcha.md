# Puesta en marcha para un estudio (fase 5)

Esta guía sirve para dejar el agente funcionando para un estudio concreto. Hay que hacerla una vez por estudio, porque en v1 cada estudio tiene su propio despliegue.

## 1. Configurar el estudio

1. Copia `config/estudio.toml` y rellénalo con los datos reales: el **plan** (`basico` o `ia`; con `basico` no hace falta `ANTHROPIC_API_KEY`), nombre, dirección, staff con sus teléfonos en formato `+34…`, tipos de clase, aforos, horario semanal, catálogo y precios, políticas y FAQ.
2. Rellena `.env` a partir de `.env.example`.
3. Crea la base de datos y carga el estudio:
   ```bash
   alembic upgrade head
   python -m app.db.seed --config config/mi-estudio.toml
   ```
   El seed genera las clases de las próximas 2 semanas y el cron diario las va extendiendo, para que siempre haya 2 semanas publicadas. Las clases puntuales o los cambios los hace el admin desde WhatsApp.

## 2. Probar en local sin WhatsApp

```bash
docker compose up -d db redis
python -m app.chat --como +34600000001     # como admin
python -m app.chat --como +34612345678     # como contacto nuevo
```

Con `PAGOS_PROVEEDOR=fake`, los enlaces de pago se simulan con `:pagar <enlace>` en la consola o abriendo `/dev/pagar/<ref>`.

## 3. WhatsApp (Meta Cloud API)

> El número queda conectado a la API y ya no se puede usar en la app de WhatsApp. Cuando una conversación se deriva a una persona, el admin recibe los mensajes en su propio WhatsApp y contesta escribiendo al asistente `responder +34… mensaje`. Para devolver el control al bot: `reactivar +34…`.


1. Crea una app de tipo *Business* en [developers.facebook.com](https://developers.facebook.com) y añade el producto **WhatsApp**.
2. Da de alta un número **nuevo** o migra el del estudio. Un número que se usa con la API ya no puede usarse en la app normal de WhatsApp.
3. Genera un **token permanente** con un usuario del sistema de Business Manager y el permiso `whatsapp_business_messaging`.
4. En `.env`, rellena `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET` (es el *App secret*) y un `WHATSAPP_VERIFY_TOKEN` que te inventes. Pon también `CANAL=whatsapp`.
5. Configura el webhook con la URL `https://TU-DOMINIO/webhook/whatsapp`, el mismo *verify token* y la suscripción al campo `messages`.
6. Crea y envía a aprobar las plantillas de `docs/plantillas-whatsapp.md`.

## 4. Pagos con Stripe

1. En `.env`: `PAGOS_PROVEEDOR=stripe`, `STRIPE_API_KEY` y `BASE_URL_PUBLICA=https://TU-DOMINIO`.
2. Crea un webhook en Stripe hacia `https://TU-DOMINIO/webhook/stripe` con los eventos `checkout.session.completed` y `checkout.session.async_payment_succeeded`, y copia el *signing secret* en `STRIPE_WEBHOOK_SECRET`.
3. Prueba primero en modo test con la tarjeta `4242 4242 4242 4242`.

## 5. Facturación (opcional)

Con Holded, pon `FACTURACION_PROVEEDOR=holded` y `HOLDED_API_KEY`. Si no se usa ningún programa, el agente avisa al admin cuando un cliente pide una factura. **Revisa con la gestoría** el IVA aplicable (el adaptador usa 21 %) y el cumplimiento de Verifactu del programa elegido.

## 6. Despliegue

Cualquier plataforma con Docker y HTTPS sirve (Railway, Render, Fly.io…). Hacen falta:
- **PostgreSQL** gestionado, con backups diarios activados.
- **Redis**.
- Servicio **app**: `uvicorn app.main:app --host 0.0.0.0 --port 8000`, con un dominio HTTPS.
- Servicio **worker**: `arq app.worker.WorkerSettings` (una sola instancia, porque lleva el cron).
- Antes de arrancar: `alembic upgrade head`.
- Los secretos van en las variables de entorno de la plataforma, nunca en el repositorio.

## 7. Antes del piloto

- [ ] Lanzar los evals con la API key real: `ANTHROPIC_API_KEY=... pytest tests/evals -s`. Revisar la latencia p95 (objetivo < 15 s) y el coste por mensaje.
- [ ] Revisar y publicar los textos legales (`docs/textos-legales.md`).
- [ ] Una semana de piloto solo con el staff y después con unos 10 clientes de confianza.
- [ ] Revisar a diario la tabla `eventos_agente` durante el piloto: errores, herramientas llamadas y tokens.
