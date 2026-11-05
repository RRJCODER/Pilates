"""Lista de espera (RF-RES-003, 004).

Cuando se libera una plaza se ofrece al primero de la lista, que tiene un plazo
para aceptarla. Mientras la oferta está en curso, la plaza cuenta como ocupada.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.modelos import Cliente, EstadoEspera, EstadoReserva, ListaEspera, Reserva
from app.servicios.base import (
    Aviso,
    Contexto,
    ErrorNegocio,
    Rol,
    a_utc,
    politicas,
    requiere_rol,
)
from app.servicios.clases import describir, obtener_clase, ocupadas, zona


@requiere_rol(Rol.CLIENTE)
def unirse(s: Session, ctx: Contexto, clase_id: int) -> dict[str, Any]:
    cid = ctx.exigir_cliente()
    clase = obtener_clase(s, ctx, clase_id, bloquear=True)
    if a_utc(clase.inicio) <= ctx.ahora:
        raise ErrorNegocio("Esa clase ya ha pasado.")
    if ocupadas(s, clase.id) < clase.aforo:
        raise ErrorNegocio("La clase tiene plazas libres; puedes reservar directamente.")
    if s.scalar(select(Reserva).where(Reserva.clase_id == clase.id, Reserva.cliente_id == cid,
                                      Reserva.estado == EstadoReserva.CONFIRMADA)):
        raise ErrorNegocio("Ya tienes plaza en esa clase.")
    existente = s.scalar(select(ListaEspera).where(ListaEspera.clase_id == clase.id,
                                                   ListaEspera.cliente_id == cid))
    posicion = (s.scalar(select(func.max(ListaEspera.posicion))
                         .where(ListaEspera.clase_id == clase.id)) or 0) + 1
    if existente:
        if existente.estado in (EstadoEspera.ESPERANDO, EstadoEspera.OFRECIDA):
            raise ErrorNegocio("Ya estás en la lista de espera de esa clase.")
        existente.estado, existente.posicion = EstadoEspera.ESPERANDO, posicion
        existente.ofrecida_en = existente.expira_en = None
    else:
        s.add(ListaEspera(estudio_id=ctx.estudio_id, clase_id=clase.id, cliente_id=cid,
                          posicion=posicion))
    s.flush()
    delante = s.scalar(select(func.count()).select_from(ListaEspera).where(
        ListaEspera.clase_id == clase.id, ListaEspera.estado == EstadoEspera.ESPERANDO,
        ListaEspera.posicion < posicion)) or 0
    return {"resultado": "ok", **describir(s, clase, zona(s, ctx.estudio_id)),
            "personas_delante": delante}


def ofrecer_siguiente(s: Session, ctx: Contexto, clase_id: int) -> dict[str, Any] | None:
    """Si hay plaza libre, se la ofrece al siguiente de la lista (genera un Aviso)."""
    clase = obtener_clase(s, ctx, clase_id, bloquear=True)
    if a_utc(clase.inicio) <= ctx.ahora or ocupadas(s, clase.id) >= clase.aforo:
        return None
    siguiente = s.scalars(select(ListaEspera).where(
        ListaEspera.clase_id == clase.id, ListaEspera.estado == EstadoEspera.ESPERANDO)
        .order_by(ListaEspera.posicion)).first()
    if siguiente is None:
        return None
    horas = float(politicas(s, ctx.estudio_id)["lista_espera_horas_para_aceptar"])
    # Nunca más allá de 1 h antes de la clase
    expira = min(ctx.ahora + timedelta(hours=horas), a_utc(clase.inicio) - timedelta(hours=1))
    if expira <= ctx.ahora:
        expira = a_utc(clase.inicio)
    siguiente.estado, siguiente.ofrecida_en, siguiente.expira_en = (
        EstadoEspera.OFRECIDA, ctx.ahora, expira)
    cli = s.get(Cliente, siguiente.cliente_id)
    d = describir(s, clase, zona(s, ctx.estudio_id))
    if cli:
        ctx.avisos.append(Aviso(
            cli.telefono,
            f"¡Se ha liberado una plaza! 🎉 {d['clase']} el {d['dia']} a las {d['hora']}. "
            f"¿La quieres? Responde SÍ antes de las "
            f"{expira.astimezone(zona(s, ctx.estudio_id)).strftime('%H:%M')}.",
            plantilla="plaza_liberada", parametros=[d["clase"], d["dia"], d["hora"]]))
    return {"ofrecida_a": siguiente.cliente_id, "expira_en": expira.isoformat()}


def expirar_ofertas(s: Session, ctx: Contexto) -> int:
    """Cron: caduca ofertas no aceptadas y pasa la plaza al siguiente."""
    caducadas = list(s.scalars(select(ListaEspera).where(
        ListaEspera.estudio_id == ctx.estudio_id, ListaEspera.estado == EstadoEspera.OFRECIDA,
        ListaEspera.expira_en < ctx.ahora)))
    for e in caducadas:
        e.estado = EstadoEspera.EXPIRADA
    s.flush()
    for clase_id in {e.clase_id for e in caducadas}:
        ofrecer_siguiente(s, ctx, clase_id)
    return len(caducadas)
