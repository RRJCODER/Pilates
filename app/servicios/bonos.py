"""Bonos y mensualidades (RF-CLI-002, 003, 007)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.modelos import Bono, EstadoBono, Pago, Producto
from app.servicios.acciones import crear_accion, ejecutor
from app.servicios.base import (
    Contexto,
    ErrorNegocio,
    NoEncontrado,
    Rol,
    politicas,
    requiere_rol,
)


def describir_bono(b: Bono) -> dict[str, Any]:
    return {
        "bono_id": b.id,
        "producto": b.producto.nombre,
        "tipo": b.producto.tipo,
        "sesiones_total": b.sesiones_total,
        "sesiones_usadas": b.sesiones_usadas,
        "sesiones_restantes": b.sesiones_restantes,
        "caduca": b.caduca.isoformat() if b.caduca else None,
        "estado": b.estado,
        "congelado_hasta": b.congelado_hasta.isoformat() if b.congelado_hasta else None,
    }


def bono_para_fecha(s: Session, cliente_id: int, fecha: date, *,
                    bloquear: bool = False) -> Bono | None:
    """El bono activo que caduca antes y cubre esa fecha (se gasta primero el más urgente)."""
    q = (select(Bono).where(Bono.cliente_id == cliente_id, Bono.estado == EstadoBono.ACTIVO,
                            Bono.sesiones_usadas < Bono.sesiones_total,
                            Bono.caduca >= fecha)
         .order_by(Bono.caduca, Bono.id))
    if bloquear:
        q = q.with_for_update()
    return s.scalars(q).first()


def gastar_sesion(bono: Bono) -> None:
    bono.sesiones_usadas += 1
    if bono.sesiones_usadas >= bono.sesiones_total:
        bono.estado = EstadoBono.AGOTADO


@requiere_rol(Rol.LEAD)
def catalogo(s: Session, ctx: Contexto) -> list[dict[str, Any]]:
    return [{"producto": p.clave, "nombre": p.nombre, "tipo": p.tipo, "sesiones": p.sesiones,
             "validez_dias": p.validez_dias, "precio_eur": float(p.precio)}
            for p in s.scalars(select(Producto).where(Producto.estudio_id == ctx.estudio_id,
                                                      Producto.activo.is_(True))
                               .order_by(Producto.precio))]


def producto_por_clave(s: Session, estudio_id: int, clave: str) -> Producto:
    p = s.scalar(select(Producto).where(Producto.estudio_id == estudio_id,
                                        Producto.clave == clave, Producto.activo.is_(True)))
    if p is None:
        raise NoEncontrado(f"No existe el producto '{clave}'. Consulta el catálogo.")
    return p


@requiere_rol(Rol.CLIENTE)
def estado_bono(s: Session, ctx: Contexto) -> dict[str, Any]:
    cid = ctx.exigir_cliente()
    bonos = list(s.scalars(select(Bono).where(
        Bono.cliente_id == cid,
        Bono.estado.in_([EstadoBono.ACTIVO, EstadoBono.CONGELADO, EstadoBono.PENDIENTE_PAGO]))
        .order_by(Bono.caduca)))
    return {
        "bonos": [describir_bono(b) for b in bonos],
        "sesiones_disponibles": sum(b.sesiones_restantes for b in bonos
                                    if b.estado == EstadoBono.ACTIVO),
    }


def activar_producto(s: Session, pago: Pago, hoy: date) -> Bono:
    """Crea el bono pagado. Solo se llama tras un pago confirmado (RF-COB-002)."""
    existente = s.scalar(select(Bono).where(Bono.pago_id == pago.id))
    if existente is not None:
        return existente
    if pago.producto_id is None:
        raise ErrorNegocio("El pago no tiene producto asociado.")
    prod = s.get(Producto, pago.producto_id)
    assert prod is not None
    # Si ya tiene un bono activo del mismo tipo, el nuevo empieza cuando caduque el anterior
    # solo para mensualidades; los bonos de sesiones empiezan hoy.
    inicio = hoy
    if prod.tipo == "mensualidad":
        ultimo = s.scalar(select(Bono).where(
            Bono.cliente_id == pago.cliente_id, Bono.estado == EstadoBono.ACTIVO,
            Bono.producto_id == prod.id).order_by(Bono.caduca.desc()))
        if ultimo and ultimo.caduca and ultimo.caduca >= hoy:
            inicio = ultimo.caduca + timedelta(days=1)
    bono = Bono(estudio_id=pago.estudio_id, cliente_id=pago.cliente_id, producto_id=prod.id,
                sesiones_total=prod.sesiones, sesiones_usadas=0, inicio=inicio,
                caduca=inicio + timedelta(days=prod.validez_dias), estado=EstadoBono.ACTIVO,
                pago_id=pago.id)
    s.add(bono)
    s.flush()
    return bono


@requiere_rol(Rol.CLIENTE)
def congelar_bono(s: Session, ctx: Contexto, motivo: str, dias: int) -> dict[str, Any]:
    pol = politicas(s, ctx.estudio_id)
    if not pol["congelacion_permitida"]:
        raise ErrorNegocio("En este estudio no se pueden congelar bonos.")
    if dias < 1 or dias > int(pol["congelacion_max_dias"]):
        raise ErrorNegocio(f"Se puede congelar entre 1 y {pol['congelacion_max_dias']} días.")
    cid = ctx.exigir_cliente()
    bono = bono_para_fecha(s, cid, ctx.ahora.date())
    if bono is None:
        raise ErrorNegocio("No tienes ningún bono activo que congelar.")
    return crear_accion(s, ctx, "congelar_bono",
                        f"Congelar tu {bono.producto.nombre} {dias} días ({motivo}). "
                        f"La caducidad se amplía {dias} días.",
                        {"bono_id": bono.id, "dias": dias, "motivo": motivo})


@ejecutor("congelar_bono")
def _ejecutar_congelar(s: Session, ctx: Contexto, p: dict[str, Any]) -> dict[str, Any]:
    bono = s.get(Bono, int(p["bono_id"]), with_for_update=True)
    if bono is None or bono.estado != EstadoBono.ACTIVO:
        raise ErrorNegocio("El bono ya no está activo.")
    dias = int(p["dias"])
    bono.estado = EstadoBono.CONGELADO
    bono.congelado_hasta = ctx.ahora.date() + timedelta(days=dias)
    if bono.caduca:
        bono.caduca = bono.caduca + timedelta(days=dias)
    return {"bono": describir_bono(bono)}


def mantenimiento_bonos(s: Session, estudio_id: int, hoy: date) -> dict[str, int]:
    """Cron diario: descongela y caduca bonos."""
    descongelados = caducados = 0
    for b in s.scalars(select(Bono).where(Bono.estudio_id == estudio_id,
                                          Bono.estado == EstadoBono.CONGELADO,
                                          Bono.congelado_hasta < hoy)):
        b.estado, b.congelado_hasta = EstadoBono.ACTIVO, None
        descongelados += 1
    for b in s.scalars(select(Bono).where(Bono.estudio_id == estudio_id,
                                          Bono.estado == EstadoBono.ACTIVO, Bono.caduca < hoy)):
        b.estado = EstadoBono.CADUCADO
        caducados += 1
    return {"descongelados": descongelados, "caducados": caducados}
