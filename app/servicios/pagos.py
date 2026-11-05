"""Cobros y facturación (RF-COB-001..005).

Regla de oro (RF-COB-002): ningún agente marca un pago como pagado. Solo lo hacen
el webhook del proveedor (`confirmar_pago_proveedor`) o un admin con confirmación
(`registrar_pago_manual`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.modelos import (
    Bono,
    Cliente,
    EstadoBono,
    EstadoPago,
    Pago,
    Producto,
)
from app.integraciones.facturacion import DatosFactura, get_facturacion
from app.integraciones.pagos import get_pagos
from app.servicios.acciones import crear_accion, ejecutor
from app.servicios.base import (
    Aviso,
    Contexto,
    ErrorNegocio,
    NoEncontrado,
    Rol,
    requiere_rol,
)
from app.servicios.bonos import activar_producto, describir_bono, producto_por_clave
from app.servicios.clientes import telefonos_admin


def _crear_pago_con_enlace(s: Session, cli: Cliente, prod: Producto) -> Pago:
    pago = Pago(estudio_id=cli.estudio_id, cliente_id=cli.id, producto_id=prod.id,
                importe=prod.precio, metodo=get_pagos().nombre, estado=EstadoPago.PENDIENTE)
    s.add(pago)
    s.flush()
    enlace = get_pagos().crear_enlace(pago_id=pago.id, concepto=prod.nombre,
                                      importe=prod.precio, telefono=cli.telefono,
                                      email=cli.email)
    pago.enlace_pago, pago.proveedor_ref = enlace.url, enlace.referencia
    return pago


@requiere_rol(Rol.CLIENTE)
def crear_enlace_pago(s: Session, ctx: Contexto, producto: str) -> dict[str, Any]:
    """RF-COB-001. `producto` es la clave del catálogo (p. ej. 'bono10')."""
    cli = s.get(Cliente, ctx.exigir_cliente())
    assert cli is not None
    prod = producto_por_clave(s, ctx.estudio_id, producto)
    # Reutiliza un enlace pendiente reciente del mismo producto
    previo = s.scalar(select(Pago).where(
        Pago.cliente_id == cli.id, Pago.producto_id == prod.id,
        Pago.estado == EstadoPago.PENDIENTE, Pago.creado_en > ctx.ahora - timedelta(hours=24)))
    pago = previo or _crear_pago_con_enlace(s, cli, prod)
    return {"resultado": "ok", "pago_id": pago.id, "producto": prod.nombre,
            "importe_eur": float(prod.precio), "enlace_pago": pago.enlace_pago,
            "nota": "El producto se activará automáticamente al confirmarse el pago."}


def confirmar_pago_proveedor(s: Session, referencia: str, ahora: datetime) -> dict[str, Any]:
    """RF-COB-002. Solo lo llama el webhook del proveedor (firma verificada). Idempotente."""
    pago = s.scalar(select(Pago).where(Pago.proveedor_ref == referencia).with_for_update())
    if pago is None:
        raise NoEncontrado(f"Pago con referencia {referencia} no encontrado")
    ctx = Contexto.sistema(pago.estudio_id, ahora)
    if pago.estado == EstadoPago.PAGADO:
        return {"resultado": "ya_procesado", "pago_id": pago.id, "avisos": []}
    pago.estado, pago.pagado_en = EstadoPago.PAGADO, ahora
    resultado = _tras_pago(s, ctx, pago)
    return {**resultado, "avisos": ctx.avisos}


def _tras_pago(s: Session, ctx: Contexto, pago: Pago) -> dict[str, Any]:
    cli = s.get(Cliente, pago.cliente_id)
    assert cli is not None
    bono = activar_producto(s, pago, ctx.ahora.date())
    prod = bono.producto
    try:
        pago.factura_ref = get_facturacion().emitir(DatosFactura(
            pago_id=pago.id, nombre=cli.nombre, email=cli.email, telefono=cli.telefono,
            concepto=prod.nombre, importe=pago.importe))
    except Exception as e:  # la factura se puede reintentar; el pago ya es válido
        for tel in telefonos_admin(s, ctx.estudio_id):
            ctx.avisos.append(Aviso(tel, f"⚠️ No se pudo emitir la factura del pago {pago.id}: {e}"))
    texto = (f"✅ ¡Pago recibido! Tu {prod.nombre} ya está activo: {bono.sesiones_total} "
             f"sesiones hasta el {bono.caduca:%d/%m/%Y}. ¿Te reservo alguna clase?")
    ctx.avisos.append(Aviso(cli.telefono, texto, plantilla="pago_confirmado",
                            parametros=[prod.nombre, f"{bono.caduca:%d/%m/%Y}"]))
    return {"resultado": "ok", "pago_id": pago.id, "bono": describir_bono(bono)}


@requiere_rol(Rol.CLIENTE)
def pagos_pendientes(s: Session, ctx: Contexto) -> list[dict[str, Any]]:
    cid = ctx.exigir_cliente()
    return [{"pago_id": p.id, "importe_eur": float(p.importe), "enlace_pago": p.enlace_pago,
             "creado": p.creado_en.date().isoformat()}
            for p in s.scalars(select(Pago).where(Pago.cliente_id == cid,
                                                  Pago.estado == EstadoPago.PENDIENTE))]


@requiere_rol(Rol.CLIENTE)
def enviar_factura(s: Session, ctx: Contexto, pago_id: int | None = None) -> dict[str, Any]:
    """RF-COB-004. Sin pago_id, la del último pago."""
    cid = ctx.exigir_cliente()
    q = select(Pago).where(Pago.cliente_id == cid, Pago.estado == EstadoPago.PAGADO)
    if pago_id is not None:
        q = q.where(Pago.id == pago_id)
    pago = s.scalars(q.order_by(Pago.pagado_en.desc())).first()
    if pago is None:
        raise NoEncontrado("No encuentro ese pago.")
    if not pago.factura_ref:
        for tel in telefonos_admin(s, ctx.estudio_id):
            ctx.avisos.append(Aviso(tel, f"🧾 {ctx.nombre} ({ctx.telefono}) pide la factura del "
                                         f"pago {pago.id} ({pago.importe} €)."))
        return {"resultado": "solicitada_al_estudio",
                "mensaje": "He pedido la factura al estudio; te la enviarán en breve."}
    url = get_facturacion().url_pdf(pago.factura_ref)
    return {"resultado": "ok", "factura": pago.factura_ref, "url": url,
            "mensaje": None if url else "La factura se envió a tu email."}


@requiere_rol(Rol.ADMIN)
def registrar_pago_manual(s: Session, ctx: Contexto, cliente_id: int, producto: str,
                          metodo: str, importe_eur: float | None = None) -> dict[str, Any]:
    cli = s.get(Cliente, cliente_id)
    if cli is None or cli.estudio_id != ctx.estudio_id:
        raise NoEncontrado("No encuentro ese cliente.")
    prod = producto_por_clave(s, ctx.estudio_id, producto)
    importe = Decimal(str(importe_eur)) if importe_eur is not None else prod.precio
    return crear_accion(s, ctx, "pago_manual",
                        f"Registrar pago de {importe} € en {metodo} de {cli.nombre} por "
                        f"{prod.nombre} y activarlo",
                        {"cliente_id": cli.id, "producto_id": prod.id, "metodo": metodo,
                         "importe": str(importe)})


@ejecutor("pago_manual")
def _ejecutar_pago_manual(s: Session, ctx: Contexto, p: dict[str, Any]) -> dict[str, Any]:
    pago = Pago(estudio_id=ctx.estudio_id, cliente_id=int(p["cliente_id"]),
                producto_id=int(p["producto_id"]), importe=Decimal(p["importe"]),
                metodo=str(p["metodo"]), estado=EstadoPago.PAGADO, pagado_en=ctx.ahora)
    s.add(pago)
    s.flush()
    return _tras_pago(s, ctx, pago)


@requiere_rol(Rol.ADMIN)
def devolver_pago(s: Session, ctx: Contexto, pago_id: int, motivo: str) -> dict[str, Any]:
    pago = s.get(Pago, pago_id)
    if pago is None or pago.estudio_id != ctx.estudio_id:
        raise NoEncontrado("No encuentro ese pago.")
    if pago.estado != EstadoPago.PAGADO:
        raise ErrorNegocio(f"Ese pago está {pago.estado}; no se puede devolver.")
    cli = s.get(Cliente, pago.cliente_id)
    assert cli is not None
    return crear_accion(s, ctx, "devolver_pago",
                        f"Devolver {pago.importe} € a {cli.nombre} (pago {pago.id}, {motivo}). "
                        "El bono asociado quedará anulado.",
                        {"pago_id": pago.id, "motivo": motivo})


@ejecutor("devolver_pago")
def _ejecutar_devolucion(s: Session, ctx: Contexto, p: dict[str, Any]) -> dict[str, Any]:
    pago = s.get(Pago, int(p["pago_id"]), with_for_update=True)
    if pago is None or pago.estado != EstadoPago.PAGADO:
        raise ErrorNegocio("El pago ya no se puede devolver.")
    if pago.proveedor_ref and pago.metodo == get_pagos().nombre:
        get_pagos().devolver(pago.proveedor_ref, pago.importe)
    pago.estado = EstadoPago.DEVUELTO
    for b in s.scalars(select(Bono).where(Bono.pago_id == pago.id)):
        b.estado = EstadoBono.ANULADO
    cli = s.get(Cliente, pago.cliente_id)
    if cli:
        ctx.avisos.append(Aviso(cli.telefono, f"Te hemos devuelto {pago.importe} €. "
                                              "Puede tardar unos días en aparecer en tu cuenta."))
    return {"devuelto_eur": float(pago.importe), "metodo": pago.metodo}


@requiere_rol(Rol.ADMIN)
def resumen_caja(s: Session, ctx: Contexto, desde: date, hasta: date) -> dict[str, Any]:
    """`hasta` incluido."""
    ini = datetime.combine(desde, datetime.min.time(), tzinfo=ctx.ahora.tzinfo)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=ctx.ahora.tzinfo)
    filas = s.execute(select(Pago.metodo, Pago.estado, func.count(), func.sum(Pago.importe))
                      .where(Pago.estudio_id == ctx.estudio_id,
                             func.coalesce(Pago.pagado_en, Pago.creado_en) >= ini,
                             func.coalesce(Pago.pagado_en, Pago.creado_en) < fin)
                      .group_by(Pago.metodo, Pago.estado)).all()
    detalle: list[dict[str, Any]] = [
        {"metodo": m, "estado": e, "n": n, "total_eur": float(t or 0)} for m, e, n, t in filas]

    def total(estado: EstadoPago) -> float:
        return sum(float(d["total_eur"]) for d in detalle if d["estado"] == estado)

    cobrado, devuelto = total(EstadoPago.PAGADO), total(EstadoPago.DEVUELTO)
    pendiente = total(EstadoPago.PENDIENTE)
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "cobrado_eur": cobrado,
            "devuelto_eur": devuelto, "pendiente_eur": pendiente, "detalle": detalle}


# ------------------------------------------------------------------ cron


def generar_renovaciones(s: Session, ctx: Contexto, hoy: date) -> int:
    """RF-COB-003: el día que vence una mensualidad se genera el cobro de la siguiente."""
    n = 0
    vencen = s.scalars(select(Bono).join(Producto).where(
        Bono.estudio_id == ctx.estudio_id, Producto.tipo == "mensualidad",
        Bono.caduca == hoy, Bono.estado.in_([EstadoBono.ACTIVO, EstadoBono.AGOTADO])))
    for b in vencen:
        cli = s.get(Cliente, b.cliente_id)
        if cli is None or not b.producto.activo:
            continue
        ya = s.scalar(select(Pago).where(Pago.cliente_id == cli.id,
                                         Pago.producto_id == b.producto_id,
                                         Pago.creado_en >= ctx.ahora - timedelta(days=5)))
        if ya:
            continue
        pago = _crear_pago_con_enlace(s, cli, b.producto)
        n += 1
        ctx.avisos.append(Aviso(
            cli.telefono,
            f"Hola {cli.nombre.split()[0]} 👋 hoy se renueva tu {b.producto.nombre} "
            f"({b.producto.precio} €). Puedes pagarla aquí: {pago.enlace_pago}",
            plantilla="pago_pendiente",
            parametros=[cli.nombre.split()[0], b.producto.nombre, str(b.producto.precio),
                        pago.enlace_pago or ""]))
    return n


def recordar_impagos(s: Session, ctx: Contexto, hoy: date, dias: list[int]) -> int:
    """RF-COB-003: recuerda en los días configurados y después avisa al admin."""
    n = 0
    ultimo = max(dias)
    for pago in s.scalars(select(Pago).where(Pago.estudio_id == ctx.estudio_id,
                                             Pago.estado == EstadoPago.PENDIENTE)):
        antiguedad = (hoy - pago.creado_en.date()).days
        cli = s.get(Cliente, pago.cliente_id)
        if cli is None:
            continue
        if antiguedad in dias and antiguedad > 0:
            n += 1
            ctx.avisos.append(Aviso(
                cli.telefono,
                f"Recordatorio: tienes pendiente un pago de {pago.importe} €. "
                f"Enlace: {pago.enlace_pago}",
                plantilla="pago_pendiente",
                parametros=[cli.nombre.split()[0], "tu pago", str(pago.importe),
                            pago.enlace_pago or ""]))
        elif antiguedad == ultimo + 1:
            for tel in telefonos_admin(s, ctx.estudio_id):
                ctx.avisos.append(Aviso(tel, f"💸 {cli.nombre} ({cli.telefono}) lleva "
                                             f"{antiguedad} días sin pagar {pago.importe} €."))
    return n
