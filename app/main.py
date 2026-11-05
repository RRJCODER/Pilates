"""API web: webhooks de WhatsApp y Stripe (diseño §8, RF-COB-002)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.conversacion import entregar
from app.db.sesion import sesion
from app.servicios.base import NoEncontrado
from app.servicios.pagos import confirmar_pago_proveedor
from app.webhooks.cola import encolar_mensaje
from config.settings import get_settings

log = logging.getLogger(__name__)
app = FastAPI(title="Pilates · agente de IA")


@app.get("/salud")
def salud() -> dict[str, str]:
    return {"estado": "ok"}


# ------------------------------------------------------------------ WhatsApp


@app.get("/webhook/whatsapp")
def verificar_whatsapp(modo: str = Query(alias="hub.mode", default=""),
                       token: str = Query(alias="hub.verify_token", default=""),
                       reto: str = Query(alias="hub.challenge", default="")) -> Response:
    cfg = get_settings()
    if modo == "subscribe" and cfg.whatsapp_verify_token and token == cfg.whatsapp_verify_token:
        return PlainTextResponse(reto)
    raise HTTPException(403, "Token de verificación incorrecto")


@app.post("/webhook/whatsapp")
async def recibir_whatsapp(request: Request) -> dict[str, int]:
    """Verifica la firma, extrae los mensajes y los encola. Responde rápido a Meta."""
    from app.integraciones.canal import firma_valida

    cuerpo = await request.body()
    if not firma_valida(cuerpo, request.headers.get("X-Hub-Signature-256"),
                        get_settings().whatsapp_app_secret):
        raise HTTPException(401, "Firma no válida")
    datos = await request.json()
    n = 0
    for mensaje in extraer_mensajes(datos):
        await encolar_mensaje(mensaje)
        n += 1
    return {"encolados": n}


def extraer_mensajes(datos: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for entry in datos.get("entry", []):
        for change in entry.get("changes", []):
            valor = change.get("value", {})
            for m in valor.get("messages", []):
                tipo = m.get("type")
                base = {"wa_message_id": m["id"], "telefono": "+" + m["from"].lstrip("+"),
                        "tipo": tipo}
                if tipo == "text":
                    base["texto"] = m["text"]["body"]
                elif tipo == "button":  # botón de respuesta rápida de una plantilla
                    base["texto"] = m["button"].get("text", "")
                    if m["button"].get("payload"):
                        base["respuesta_id"] = m["button"]["payload"]
                elif tipo == "interactive":  # botón o lista (RF-PLAN-002)
                    i = m["interactive"]
                    elegido = i.get("button_reply") or i.get("list_reply") or {}
                    base["texto"] = elegido.get("title", "")
                    base["respuesta_id"] = elegido.get("id")
                elif tipo in ("audio", "voice"):
                    base["media_id"] = m[tipo]["id"]
                elif tipo in ("image", "document"):
                    base["media_id"] = m[tipo]["id"]
                    base["texto"] = m[tipo].get("caption", "")
                else:
                    base["texto"] = f"[mensaje de tipo {tipo} no soportado]"
                out.append(base)
    return out


# ------------------------------------------------------------------ pagos


@app.post("/webhook/stripe")
async def webhook_stripe(request: Request) -> dict[str, str]:
    """RF-COB-002: la única vía (junto al pago manual del admin) para dar un pago por hecho."""
    import stripe

    cfg = get_settings()
    cuerpo = await request.body()
    try:
        evento = stripe.Webhook.construct_event(
            cuerpo, request.headers.get("Stripe-Signature", ""), cfg.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as e:
        raise HTTPException(400, "Firma de Stripe no válida") from e
    if evento["type"] in ("checkout.session.completed",
                          "checkout.session.async_payment_succeeded"):
        sesion_stripe = evento["data"]["object"]
        if sesion_stripe.get("payment_status") == "paid":
            _confirmar(sesion_stripe["id"])
    return {"estado": "ok"}


@app.post("/dev/pagar/{referencia}")
@app.get("/dev/pagar/{referencia}", response_class=HTMLResponse)
def pagar_fake(referencia: str) -> str:
    """Simula el webhook del proveedor cuando PAGOS_PROVEEDOR=fake (solo desarrollo)."""
    if get_settings().pagos_proveedor != "fake":
        raise HTTPException(404)
    _confirmar(referencia)
    return "<h1>✅ Pago simulado</h1><p>Vuelve a WhatsApp.</p>"


def _confirmar(referencia: str) -> None:
    try:
        with sesion() as s:
            res = confirmar_pago_proveedor(s, referencia, datetime.now(UTC))
    except NoEncontrado as e:
        log.warning("Pago desconocido: %s", referencia)
        raise HTTPException(404, "Pago no encontrado") from e
    with sesion() as s:
        from app.db.modelos import Pago

        pago = s.query(Pago).filter_by(proveedor_ref=referencia).one()
        estudio_id = pago.estudio_id
    entregar(estudio_id, res["avisos"])


@app.get("/pago/ok", response_class=HTMLResponse)
def pago_ok() -> str:
    return "<h1>¡Gracias! 💛</h1><p>Recibirás la confirmación por WhatsApp.</p>"


@app.get("/pago/cancelado", response_class=HTMLResponse)
def pago_cancelado() -> str:
    return "<h1>Pago cancelado</h1><p>Puedes volver a intentarlo desde WhatsApp.</p>"
