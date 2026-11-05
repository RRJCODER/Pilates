"""Reservas (RF-RES-002, 005, 006).

La reserva bloquea la clase y el bono (`SELECT ... FOR UPDATE`) dentro de la misma
transacción, así que dos personas no pueden quedarse con la última plaza (E-RES-05).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.modelos import (
    Clase,
    EstadoClase,
    EstadoEspera,
    EstadoReserva,
    ListaEspera,
    Reserva,
    TipoClase,
)
from app.servicios import lista_espera
from app.servicios.base import (
    Contexto,
    ErrorNegocio,
    NoEncontrado,
    Rol,
    a_utc,
    politicas,
    requiere_rol,
)
from app.servicios.bonos import bono_para_fecha, gastar_sesion
from app.servicios.clases import (
    describir,
    devolver_sesion,
    obtener_clase,
    ocupadas,
    zona,
)


class ClaseLlena(ErrorNegocio):
    pass


def _reservar(s: Session, ctx: Contexto, cliente_id: int, clase_id: int, *,
              desde_lista_espera: bool = False) -> dict[str, Any]:
    clase = obtener_clase(s, ctx, clase_id, bloquear=True)
    if clase.estado != EstadoClase.PROGRAMADA:
        raise ErrorNegocio("Esa clase está cancelada.")
    if a_utc(clase.inicio) <= ctx.ahora:
        raise ErrorNegocio("Esa clase ya ha empezado o ha pasado.")

    existente = s.scalar(select(Reserva).where(Reserva.clase_id == clase.id,
                                               Reserva.cliente_id == cliente_id))
    if existente and existente.estado == EstadoReserva.CONFIRMADA:
        raise ErrorNegocio("Ya tienes plaza en esa clase.")

    pol = politicas(s, ctx.estudio_id)
    futuras = s.scalar(select(func.count()).select_from(Reserva).join(Clase).where(
        Reserva.cliente_id == cliente_id, Reserva.estado == EstadoReserva.CONFIRMADA,
        Clase.inicio > ctx.ahora)) or 0
    if futuras >= int(pol["max_reservas_futuras"]):
        raise ErrorNegocio(f"Ya tienes {futuras} reservas futuras, que es el máximo.")

    libres = clase.aforo - ocupadas(s, clase.id)
    if desde_lista_espera:
        libres += 1  # su propia oferta ya ocupaba la plaza
    if libres <= 0:
        raise ClaseLlena("La clase está completa.")

    tz = zona(s, ctx.estudio_id)
    fecha_clase = a_utc(clase.inicio).astimezone(tz).date()
    bono = bono_para_fecha(s, cliente_id, fecha_clase, bloquear=True)
    if bono is None:
        raise ErrorNegocio("No tienes sesiones disponibles en un bono válido para esa fecha. "
                           "Puedes comprar un bono o una clase suelta.")
    gastar_sesion(bono)
    if existente:  # reactivar una reserva cancelada (UNIQUE clase+cliente)
        existente.estado, existente.bono_id = EstadoReserva.CONFIRMADA, bono.id
        existente.cancelada_en = None
        reserva = existente
    else:
        reserva = Reserva(estudio_id=ctx.estudio_id, clase_id=clase.id, cliente_id=cliente_id,
                          bono_id=bono.id)
        s.add(reserva)
    s.flush()
    return {"resultado": "ok", "reserva_id": reserva.id, **describir(s, clase, tz),
            "sesiones_restantes_bono": bono.sesiones_restantes,
            "bono_caduca": bono.caduca.isoformat() if bono.caduca else None}


@requiere_rol(Rol.CLIENTE)
def reservar_clase(s: Session, ctx: Contexto, clase_id: int) -> dict[str, Any]:
    cid = ctx.exigir_cliente()
    try:
        return _reservar(s, ctx, cid, clase_id)
    except ClaseLlena:
        clase = obtener_clase(s, ctx, clase_id)
        alternativas = _alternativas(s, ctx, clase)
        return {"resultado": "clase_llena", "puede_unirse_a_lista_espera": True,
                "alternativas": alternativas}


@requiere_rol(Rol.CLIENTE)
def aceptar_plaza_lista_espera(s: Session, ctx: Contexto, clase_id: int) -> dict[str, Any]:
    cid = ctx.exigir_cliente()
    entrada = s.scalar(select(ListaEspera).where(
        ListaEspera.clase_id == clase_id, ListaEspera.cliente_id == cid,
        ListaEspera.estado == EstadoEspera.OFRECIDA))
    if entrada is None or entrada.expira_en is None or a_utc(entrada.expira_en) < ctx.ahora:
        raise ErrorNegocio("No tienes ninguna plaza ofrecida en esa clase o la oferta caducó.")
    res = _reservar(s, ctx, cid, clase_id, desde_lista_espera=True)
    entrada.estado = EstadoEspera.ACEPTADA
    return res


@requiere_rol(Rol.CLIENTE)
def cancelar_reserva(s: Session, ctx: Contexto, reserva_id: int) -> dict[str, Any]:
    cid = ctx.exigir_cliente()
    r = s.get(Reserva, reserva_id, with_for_update=True)
    if r is None or r.cliente_id != cid:
        raise NoEncontrado("No encuentro esa reserva entre las tuyas.")
    if r.estado != EstadoReserva.CONFIRMADA:
        raise ErrorNegocio("Esa reserva ya no está activa.")
    clase = obtener_clase(s, ctx, r.clase_id)
    inicio = a_utc(clase.inicio)
    if inicio <= ctx.ahora:
        raise ErrorNegocio("La clase ya ha empezado; no se puede cancelar.")

    pol = politicas(s, ctx.estudio_id)
    horas = (inicio - ctx.ahora).total_seconds() / 3600
    limite = float(pol["cancelacion_horas_limite"])
    a_tiempo = horas >= limite
    devuelta = a_tiempo or pol["cancelacion_tardia"] == "sin_penalizacion"
    r.estado, r.cancelada_en = EstadoReserva.CANCELADA, ctx.ahora
    if devuelta:
        devolver_sesion(s, r.bono_id)
    s.flush()
    lista_espera.ofrecer_siguiente(s, ctx, clase.id)  # RF-RES-004
    return {"resultado": "ok", "cancelada": describir(s, clase, zona(s, ctx.estudio_id)),
            "sesion_devuelta": devuelta,
            "politica": f"Cancelando con menos de {limite:g} h de antelación la sesión "
                        + ("no se devuelve." if pol["cancelacion_tardia"] == "pierde_sesion"
                           else "se devuelve igualmente.")}


@requiere_rol(Rol.CLIENTE)
def reservar_recurrente(s: Session, ctx: Contexto, dia_semana: int, hora: str,
                        semanas: int = 4, modalidad: str | None = None) -> dict[str, Any]:
    """dia_semana: 0=lunes … 6=domingo. hora: 'HH:MM'."""
    cid = ctx.exigir_cliente()
    if not 1 <= semanas <= 12:
        raise ErrorNegocio("Puedo reservar de 1 a 12 semanas seguidas.")
    tz = zona(s, ctx.estudio_id)
    hh, mm = map(int, hora.split(":"))
    hoy = ctx.ahora.astimezone(tz).date()
    resultados: list[dict[str, Any]] = []
    dia = hoy + timedelta(days=(dia_semana - hoy.weekday()) % 7)
    for _ in range(semanas):
        inicio = a_utc(datetime.combine(dia, time(hh, mm), tzinfo=tz))
        q = select(Clase).join(TipoClase).where(
            Clase.estudio_id == ctx.estudio_id, Clase.inicio == inicio,
            Clase.estado == EstadoClase.PROGRAMADA)
        if modalidad:
            q = q.where(TipoClase.modalidad == modalidad.lower())
        clase = s.scalars(q).first()
        if clase is None:
            resultados.append({"fecha": dia.isoformat(), "resultado": "no_hay_clase"})
        else:
            try:
                with s.begin_nested():
                    r = _reservar(s, ctx, cid, clase.id)
                resultados.append({"fecha": dia.isoformat(), "resultado": "ok",
                                   "reserva_id": r["reserva_id"]})
            except ErrorNegocio as e:
                resultados.append({"fecha": dia.isoformat(), "resultado": "error",
                                   "motivo": str(e)})
        dia += timedelta(days=7)
    return {"reservas": resultados,
            "hechas": sum(1 for r in resultados if r["resultado"] == "ok")}


@requiere_rol(Rol.CLIENTE)
def mis_reservas(s: Session, ctx: Contexto) -> list[dict[str, Any]]:
    cid = ctx.exigir_cliente()
    tz = zona(s, ctx.estudio_id)
    q = (select(Reserva).join(Clase).where(Reserva.cliente_id == cid,
                                           Reserva.estado == EstadoReserva.CONFIRMADA,
                                           Clase.inicio > ctx.ahora).order_by(Clase.inicio))
    return [{"reserva_id": r.id, **describir(s, r.clase, tz)} for r in s.scalars(q)]


def _alternativas(s: Session, ctx: Contexto, clase: Clase, n: int = 2) -> list[dict[str, Any]]:
    """Clases parecidas (misma modalidad) en ±3 días con plazas (RF-RES-003)."""
    inicio = a_utc(clase.inicio)
    q = (select(Clase).join(TipoClase).where(
        Clase.estudio_id == ctx.estudio_id, Clase.id != clase.id,
        Clase.estado == EstadoClase.PROGRAMADA, TipoClase.modalidad == clase.tipo.modalidad,
        Clase.inicio > ctx.ahora,
        Clase.inicio.between(inicio - timedelta(days=3), inicio + timedelta(days=3))))
    tz = zona(s, ctx.estudio_id)
    candidatas = sorted(s.scalars(q), key=lambda c: abs(a_utc(c.inicio) - inicio))
    out = []
    for c in candidatas:
        d = describir(s, c, tz)
        if d["plazas_libres"] > 0:
            out.append(d)
        if len(out) == n:
            break
    return out
