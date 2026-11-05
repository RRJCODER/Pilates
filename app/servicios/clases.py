"""Clases: búsqueda, gestión por el staff y asistencia (RF-RES-001, 007, 008)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.modelos import (
    Bono,
    Clase,
    Cliente,
    EstadoBono,
    EstadoClase,
    EstadoEspera,
    EstadoReserva,
    Estudio,
    Lesion,
    ListaEspera,
    Reserva,
    Staff,
    TipoClase,
)
from app.servicios.acciones import crear_accion, ejecutor
from app.servicios.base import (
    Aviso,
    Contexto,
    ErrorNegocio,
    NoEncontrado,
    Rol,
    SinPermiso,
    a_utc,
    requiere_rol,
)

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ACTIVAS = (EstadoReserva.CONFIRMADA, EstadoReserva.ASISTIO, EstadoReserva.FALTA)


def zona(s: Session, estudio_id: int) -> ZoneInfo:
    estudio = s.get(Estudio, estudio_id)
    return ZoneInfo(estudio.zona_horaria if estudio else "Europe/Madrid")


def ocupadas(s: Session, clase_id: int) -> int:
    """Plazas ocupadas: reservas activas + ofertas de lista de espera en curso."""
    reservas = s.scalar(select(func.count()).select_from(Reserva).where(
        Reserva.clase_id == clase_id, Reserva.estado.in_(ACTIVAS))) or 0
    ofertas = s.scalar(select(func.count()).select_from(ListaEspera).where(
        ListaEspera.clase_id == clase_id, ListaEspera.estado == EstadoEspera.OFRECIDA)) or 0
    return reservas + ofertas


def obtener_clase(s: Session, ctx: Contexto, clase_id: int, *, bloquear: bool = False) -> Clase:
    q = select(Clase).where(Clase.id == clase_id, Clase.estudio_id == ctx.estudio_id)
    if bloquear:
        q = q.with_for_update()
    clase = s.scalar(q)
    if clase is None:
        raise NoEncontrado("No encuentro esa clase.")
    return clase


def describir(s: Session, clase: Clase, tz: ZoneInfo) -> dict[str, Any]:
    inicio = a_utc(clase.inicio).astimezone(tz)
    return {
        "clase_id": clase.id,
        "clase": clase.tipo.nombre,
        "modalidad": clase.tipo.modalidad,
        "nivel": clase.tipo.nivel,
        "dia": f"{DIAS[inicio.weekday()]} {inicio:%d/%m}",
        "fecha": inicio.date().isoformat(),
        "hora": inicio.strftime("%H:%M"),
        "instructor": clase.instructor.nombre,
        "plazas_libres": max(clase.aforo - ocupadas(s, clase.id), 0),
        "estado": clase.estado,
    }


@requiere_rol(Rol.LEAD)
def buscar_clases(s: Session, ctx: Contexto, desde: datetime, hasta: datetime,
                  modalidad: str | None = None, nivel: str | None = None,
                  instructor: str | None = None, solo_con_plazas: bool = False,
                  limite: int = 30) -> list[dict[str, Any]]:
    q = (select(Clase).join(TipoClase).join(Staff, Clase.instructor_id == Staff.id)
         .options(joinedload(Clase.tipo), joinedload(Clase.instructor))
         .where(Clase.estudio_id == ctx.estudio_id, Clase.estado == EstadoClase.PROGRAMADA,
                Clase.inicio >= max(desde, ctx.ahora), Clase.inicio < hasta)
         .order_by(Clase.inicio))
    if modalidad:
        q = q.where(TipoClase.modalidad == modalidad.lower())
    if nivel:
        q = q.where(TipoClase.nivel.in_([nivel.lower(), "todos"]))
    if instructor:
        q = q.where(func.lower(Staff.nombre).contains(instructor.lower()))
    tz = zona(s, ctx.estudio_id)
    out = [describir(s, c, tz) for c in s.scalars(q)]
    if solo_con_plazas:
        out = [c for c in out if c["plazas_libres"] > 0]
    return out[:limite]


@requiere_rol(Rol.ADMIN)
def crear_clase(s: Session, ctx: Contexto, tipo_clave: str, inicio: datetime,
                instructor: str, aforo: int | None = None) -> dict[str, Any]:
    tipo = s.scalar(select(TipoClase).where(TipoClase.estudio_id == ctx.estudio_id,
                                            TipoClase.clave == tipo_clave))
    if tipo is None:
        raise NoEncontrado(f"No existe el tipo de clase '{tipo_clave}'.")
    profe = _instructor(s, ctx, instructor)
    inicio = inicio.replace(tzinfo=zona(s, ctx.estudio_id)) if inicio.tzinfo is None else inicio
    inicio = a_utc(inicio)
    clase = Clase(estudio_id=ctx.estudio_id, tipo_id=tipo.id, instructor_id=profe.id,
                  inicio=inicio, fin=inicio + timedelta(minutes=tipo.duracion_min),
                  aforo=aforo or tipo.aforo)
    s.add(clase)
    s.flush()
    return {"resultado": "ok", **describir(s, clase, zona(s, ctx.estudio_id))}


@requiere_rol(Rol.ADMIN)
def modificar_clase(s: Session, ctx: Contexto, clase_id: int, inicio: datetime | None = None,
                    instructor: str | None = None, aforo: int | None = None) -> dict[str, Any]:
    clase = obtener_clase(s, ctx, clase_id, bloquear=True)
    tz = zona(s, ctx.estudio_id)
    antes = describir(s, clase, tz)
    if aforo is not None:
        if aforo < ocupadas(s, clase.id):
            raise ErrorNegocio("Hay más reservas que ese aforo; cancela reservas antes.")
        clase.aforo = aforo
    if instructor:
        clase.instructor_id = _instructor(s, ctx, instructor).id
    if inicio:
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=tz)
        inicio = a_utc(inicio)
        dur = a_utc(clase.fin) - a_utc(clase.inicio)
        clase.inicio, clase.fin = inicio, inicio + dur
    s.flush()
    s.refresh(clase)
    despues = describir(s, clase, tz)
    if inicio or instructor:
        for r in _reservas_activas(s, clase.id):
            cli = s.get(Cliente, r.cliente_id)
            if cli:
                ctx.avisos.append(Aviso(
                    cli.telefono,
                    f"Cambio en tu clase de {antes['clase']}: ahora es el {despues['dia']} a las "
                    f"{despues['hora']} con {despues['instructor']}.",
                    plantilla="cambio_clase",
                    parametros=[antes["clase"], despues["dia"], despues["hora"],
                                despues["instructor"]]))
    return {"resultado": "ok", "antes": antes, "despues": despues}


@requiere_rol(Rol.ADMIN)
def cancelar_clase(s: Session, ctx: Contexto, clase_id: int, motivo: str) -> dict[str, Any]:
    clase = obtener_clase(s, ctx, clase_id)
    d = describir(s, clase, zona(s, ctx.estudio_id))
    n = len(_reservas_activas(s, clase.id))
    return crear_accion(s, ctx, "cancelar_clase",
                        f"Cancelar {d['clase']} del {d['dia']} a las {d['hora']} "
                        f"({n} inscritos serán avisados y recuperarán su sesión)",
                        {"clase_id": clase_id, "motivo": motivo})


@ejecutor("cancelar_clase")
def _ejecutar_cancelar_clase(s: Session, ctx: Contexto, p: dict[str, Any]) -> dict[str, Any]:
    clase = obtener_clase(s, ctx, int(p["clase_id"]), bloquear=True)
    d = describir(s, clase, zona(s, ctx.estudio_id))
    clase.estado = EstadoClase.CANCELADA
    avisados = 0
    for r in _reservas_activas(s, clase.id):
        r.estado = EstadoReserva.CANCELADA
        r.cancelada_en = ctx.ahora
        devolver_sesion(s, r.bono_id)
        cli = s.get(Cliente, r.cliente_id)
        if cli:
            avisados += 1
            ctx.avisos.append(Aviso(
                cli.telefono,
                f"Lo sentimos 🙏 la clase de {d['clase']} del {d['dia']} a las {d['hora']} "
                f"se ha cancelado ({p['motivo']}). Te hemos devuelto la sesión al bono. "
                "¿Quieres que te busque otra clase?",
                plantilla="clase_cancelada",
                parametros=[d["clase"], d["dia"], d["hora"], str(p["motivo"])]))
    for e in s.scalars(select(ListaEspera).where(ListaEspera.clase_id == clase.id)):
        e.estado = EstadoEspera.RETIRADA
    return {"clase_cancelada": d, "clientes_avisados": avisados}


@requiere_rol(Rol.INSTRUCTOR)
def asistentes_clase(s: Session, ctx: Contexto, clase_id: int) -> dict[str, Any]:
    clase = obtener_clase(s, ctx, clase_id)
    if ctx.rol == Rol.INSTRUCTOR and clase.instructor_id != ctx.staff_id:
        raise SinPermiso("Solo puedes ver los asistentes de tus clases.")
    asistentes = []
    for r in _reservas_activas(s, clase.id):
        cli = s.get(Cliente, r.cliente_id)
        if cli is None:
            continue
        lesiones = [lz.descripcion for lz in s.scalars(select(Lesion).where(
            Lesion.cliente_id == cli.id, Lesion.activa.is_(True)))]
        asistentes.append({"reserva_id": r.id, "cliente": cli.nombre, "nivel": cli.nivel,
                           "estado": r.estado, "lesiones": lesiones})  # RF-CLI-004
    return {**describir(s, clase, zona(s, ctx.estudio_id)), "asistentes": asistentes}


@requiere_rol(Rol.INSTRUCTOR)
def clases_de_instructor(s: Session, ctx: Contexto, desde: datetime,
                         hasta: datetime) -> list[dict[str, Any]]:
    q = (select(Clase).where(Clase.estudio_id == ctx.estudio_id, Clase.inicio >= desde,
                             Clase.inicio < hasta, Clase.estado == EstadoClase.PROGRAMADA)
         .order_by(Clase.inicio))
    if ctx.rol == Rol.INSTRUCTOR:
        q = q.where(Clase.instructor_id == ctx.staff_id)
    tz = zona(s, ctx.estudio_id)
    return [describir(s, c, tz) for c in s.scalars(q)]


@requiere_rol(Rol.INSTRUCTOR)
def marcar_asistencia(s: Session, ctx: Contexto, reserva_id: int, asistio: bool) -> dict[str, Any]:
    r = s.get(Reserva, reserva_id)
    if r is None or r.estudio_id != ctx.estudio_id:
        raise NoEncontrado("No encuentro esa reserva.")
    clase = obtener_clase(s, ctx, r.clase_id)
    if ctx.rol == Rol.INSTRUCTOR and clase.instructor_id != ctx.staff_id:
        raise SinPermiso("Solo puedes marcar asistencia en tus clases.")
    if r.estado == EstadoReserva.CANCELADA:
        raise ErrorNegocio("Esa reserva está cancelada.")
    r.estado = EstadoReserva.ASISTIO if asistio else EstadoReserva.FALTA
    return {"resultado": "ok", "reserva_id": r.id, "estado": r.estado}


# ------------------------------------------------------------------ auxiliares


def _instructor(s: Session, ctx: Contexto, nombre: str) -> Staff:
    profe = s.scalar(select(Staff).where(Staff.estudio_id == ctx.estudio_id,
                                         func.lower(Staff.nombre).contains(nombre.lower()),
                                         Staff.activo.is_(True)))
    if profe is None:
        raise NoEncontrado(f"No encuentro a {nombre} en el staff.")
    return profe


def _reservas_activas(s: Session, clase_id: int) -> list[Reserva]:
    return list(s.scalars(select(Reserva).where(Reserva.clase_id == clase_id,
                                                Reserva.estado == EstadoReserva.CONFIRMADA)))


def devolver_sesion(s: Session, bono_id: int | None) -> None:
    if bono_id is None:
        return
    bono = s.get(Bono, bono_id, with_for_update=True)
    if bono is None or bono.sesiones_usadas == 0:
        return
    bono.sesiones_usadas -= 1
    if bono.estado == EstadoBono.AGOTADO:
        bono.estado = EstadoBono.ACTIVO
