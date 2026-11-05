"""Memoria de conversación (RF-MEM-003) y modo "en manos humanas" (RF-ORQ-004)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.modelos import Conversacion, EstadoConversacion, Mensaje, Resumen
from app.servicios.base import Aviso, Contexto, a_utc, politicas
from app.servicios.clientes import telefonos_admin

ULTIMOS = 20          # mensajes que se envían literalmente al modelo
UMBRAL_RESUMEN = 40   # a partir de cuántos mensajes sin resumir se resume


def obtener_conversacion(s: Session, ctx: Contexto) -> Conversacion:
    conv = s.scalar(select(Conversacion).where(Conversacion.estudio_id == ctx.estudio_id,
                                               Conversacion.telefono == ctx.telefono))
    if conv is None:
        conv = Conversacion(estudio_id=ctx.estudio_id, telefono=ctx.telefono,
                            cliente_id=ctx.cliente_id)
        s.add(conv)
        s.flush()
    elif ctx.cliente_id and conv.cliente_id != ctx.cliente_id:
        conv.cliente_id = ctx.cliente_id
    ctx.conversacion_id = conv.id
    return conv


def guardar_entrante(s: Session, ctx: Contexto, texto: str,
                     wa_message_id: str | None) -> bool:
    """Guarda el mensaje del usuario. False si ya se había procesado (RNF-FIA-001)."""
    conv = obtener_conversacion(s, ctx)
    try:
        with s.begin_nested():
            s.add(Mensaje(conversacion_id=conv.id, wa_message_id=wa_message_id, rol="user",
                          contenido=texto))
            s.flush()
    except IntegrityError:
        return False
    conv.ultima_entrada_en = ctx.ahora
    return True


def guardar_respuesta(s: Session, conversacion_id: int, texto: str) -> None:
    s.add(Mensaje(conversacion_id=conversacion_id, rol="assistant", contenido=texto))


@dataclass
class Historial:
    resumen: str | None
    mensajes: list[dict[str, Any]]


def historial(s: Session, conversacion_id: int) -> Historial:
    """Resumen + últimos mensajes, listos para la API (alternando user/assistant)."""
    resumen = s.scalars(select(Resumen).where(Resumen.conversacion_id == conversacion_id)
                        .order_by(Resumen.id.desc())).first()
    q = select(Mensaje).where(Mensaje.conversacion_id == conversacion_id)
    if resumen:
        q = q.where(Mensaje.id > resumen.hasta_mensaje_id)
    ultimos = list(s.scalars(q.order_by(Mensaje.id.desc()).limit(ULTIMOS)))[::-1]
    mensajes: list[dict[str, Any]] = []
    for m in ultimos:
        if mensajes and mensajes[-1]["role"] == m.rol:
            mensajes[-1]["content"] += "\n" + m.contenido
        else:
            mensajes.append({"role": m.rol, "content": m.contenido})
    while mensajes and mensajes[0]["role"] != "user":
        mensajes.pop(0)
    return Historial(resumen.contenido if resumen else None, mensajes)


def pendientes_de_resumir(s: Session, conversacion_id: int) -> list[Mensaje]:
    """Mensajes antiguos a resumir cuando hay más de UMBRAL_RESUMEN sin resumir."""
    sin_resumir = list(s.scalars(select(Mensaje).where(
        Mensaje.conversacion_id == conversacion_id, Mensaje.resumido.is_(False))
        .order_by(Mensaje.id)))
    if len(sin_resumir) <= UMBRAL_RESUMEN:
        return []
    return sin_resumir[:-ULTIMOS]


def conversaciones_a_resumir(s: Session, estudio_id: int) -> list[int]:
    return list(s.scalars(select(Mensaje.conversacion_id).join(Conversacion).where(
        Conversacion.estudio_id == estudio_id, Mensaje.resumido.is_(False))
        .group_by(Mensaje.conversacion_id).having(func.count() > UMBRAL_RESUMEN)))


def guardar_resumen(s: Session, conversacion_id: int, texto: str,
                    mensajes: list[Mensaje]) -> None:
    for m in mensajes:
        m.resumido = True
    s.add(Resumen(conversacion_id=conversacion_id, contenido=texto,
                  hasta_mensaje_id=max(m.id for m in mensajes)))


def resumen_previo(s: Session, conversacion_id: int) -> str | None:
    r = s.scalars(select(Resumen).where(Resumen.conversacion_id == conversacion_id)
                  .order_by(Resumen.id.desc())).first()
    return r.contenido if r else None


def en_manos_humanas(s: Session, ctx: Contexto) -> bool:
    conv = obtener_conversacion(s, ctx)
    if conv.estado != EstadoConversacion.HUMANO:
        return False
    if conv.humano_hasta and a_utc(conv.humano_hasta) < ctx.ahora:
        conv.estado, conv.humano_hasta = EstadoConversacion.BOT, None
        return False
    return True


def derivar_a_humano(s: Session, ctx: Contexto, motivo: str, urgente: bool) -> dict[str, Any]:
    """RF-ORQ-004: avisa al admin y silencia al bot durante unas horas."""
    conv = obtener_conversacion(s, ctx)
    horas = float(politicas(s, ctx.estudio_id)["humano_horas"])
    conv.estado = EstadoConversacion.HUMANO
    conv.humano_hasta = ctx.ahora + timedelta(hours=horas)
    icono = "🚨" if urgente else "🙋"
    quien = f"{ctx.nombre or 'Contacto sin ficha'} ({ctx.telefono})"
    for tel in telefonos_admin(s, ctx.estudio_id):
        ctx.avisos.append(Aviso(tel, f"{icono} {quien} necesita atención: {motivo}\n"
                                     f"El asistente no responderá a este número durante "
                                     f"{horas:g} h. Escribe «reactivar {ctx.telefono}» "
                                     "para devolverle el control antes."))
    return {"resultado": "ok", "horas": horas}


def reactivar_bot(s: Session, ctx: Contexto, telefono: str) -> dict[str, Any]:
    conv = s.scalar(select(Conversacion).where(Conversacion.estudio_id == ctx.estudio_id,
                                               Conversacion.telefono == telefono))
    if conv is None:
        return {"resultado": "no_encontrada"}
    conv.estado, conv.humano_hasta = EstadoConversacion.BOT, None
    return {"resultado": "ok", "telefono": telefono}


def reenviar_a_admin(s: Session, ctx: Contexto, texto: str) -> None:
    for tel in telefonos_admin(s, ctx.estudio_id):
        ctx.avisos.append(Aviso(tel, f"💬 {ctx.nombre or ctx.telefono}: {texto}"))
