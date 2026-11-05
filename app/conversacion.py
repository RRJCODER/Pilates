"""Manejador de conversación: un mensaje entra, una respuesta sale (diseño §1).

1. Identifica al remitente (código, no modelo) y deduplica el mensaje.
2. Si la conversación está en manos de una persona, reenvía y no responde.
3. Ejecuta el orquestador con el historial (resumen + últimos mensajes).
4. Envía la respuesta y los avisos a terceros por el canal adecuado.
Si algo falla, responde con un mensaje de cortesía y avisa al admin (RNF-FIA-002).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.agentes.claude import Uso
from app.agentes.orquestador import responder
from app.db.modelos import Conversacion
from app.db.sesion import sesion
from app.integraciones.canal import Canal, get_canal
from app.servicios import conversaciones
from app.servicios.base import Aviso, Contexto
from app.servicios.clientes import identificar, telefonos_admin

log = logging.getLogger(__name__)

MENSAJE_ERROR = ("Uy, ahora mismo no puedo responderte 🙏 Ya he avisado al equipo del "
                 "estudio y te escribirán en cuanto puedan.")


@dataclass
class Resultado:
    respuesta: str | None
    avisos: list[Aviso]
    uso: Uso
    duplicado: bool = False
    derivado: bool = False


def procesar_mensaje(estudio_id: int, telefono: str, texto: str,
                     wa_message_id: str | None = None, canal: Canal | None = None,
                     ahora: datetime | None = None) -> Resultado:
    canal = canal or get_canal()
    uso = Uso()
    with sesion() as s:
        ctx = identificar(s, estudio_id, telefono)
        ctx.ahora = ahora or datetime.now(UTC)
        if not conversaciones.guardar_entrante(s, ctx, texto, wa_message_id):
            return Resultado(None, [], uso, duplicado=True)
        if conversaciones.en_manos_humanas(s, ctx):
            conversaciones.reenviar_a_admin(s, ctx, texto)
            avisos = list(ctx.avisos)
            ctx.avisos.clear()
            derivado = True
        else:
            derivado = False
        hist = conversaciones.historial(s, ctx.conversacion_id or 0)
    if derivado:
        entregar(estudio_id, avisos, canal)
        return Resultado(None, avisos, uso, derivado=True)

    try:
        respuesta = responder(ctx, hist.mensajes, hist.resumen, uso)
    except Exception:
        log.exception("Error procesando mensaje de %s", telefono)
        respuesta = MENSAJE_ERROR
        with sesion() as s:
            for tel in telefonos_admin(s, estudio_id):
                ctx.avisos.append(Aviso(tel, f"⚠️ El asistente ha fallado respondiendo a "
                                             f"{ctx.nombre or telefono} ({telefono}). "
                                             f"Su mensaje: «{texto[:300]}»"))

    with sesion() as s:
        if ctx.conversacion_id:
            conversaciones.guardar_respuesta(s, ctx.conversacion_id, respuesta)
    canal.enviar_texto(telefono, respuesta)
    entregar(estudio_id, ctx.avisos, canal)
    return Resultado(respuesta, list(ctx.avisos), uso)


def entregar(estudio_id: int, avisos: list[Aviso], canal: Canal | None = None) -> None:
    """Entrega avisos: texto libre dentro de la ventana de 24 h, plantilla fuera (§8)."""
    canal = canal or get_canal()
    ahora = datetime.now(UTC)
    for a in avisos:
        try:
            with sesion() as s:
                conv = s.scalar(select(Conversacion).where(
                    Conversacion.estudio_id == estudio_id, Conversacion.telefono == a.telefono))
                ultima = conv.ultima_entrada_en if conv else None
                if ultima is not None and ultima.tzinfo is None:
                    ultima = ultima.replace(tzinfo=UTC)
                ventana_abierta = ultima is not None and ahora - ultima < timedelta(hours=23)
            if ventana_abierta or not a.plantilla:
                canal.enviar_texto(a.telefono, a.texto)
            else:
                canal.enviar_plantilla(a.telefono, a.plantilla, a.parametros)
            with sesion() as s:
                c2 = identificar(s, estudio_id, a.telefono)
                conversaciones.guardar_respuesta(
                    s, conversaciones.obtener_conversacion(s, c2).id, a.texto)
        except Exception:
            log.exception("No se pudo entregar el aviso a %s", a.telefono)


def contexto_sistema(estudio_id: int) -> Contexto:
    return Contexto.sistema(estudio_id)
