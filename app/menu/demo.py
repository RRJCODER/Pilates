"""Modo demo (RF-DEMO-*). Solo actúa con `DEMO=true`: nunca en un estudio real."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.modelos import Bono, EstadoBono, Estudio, Producto, RolStaff, Staff
from app.servicios.base import Contexto, Rol
from app.servicios.clientes import SUFIJO_DEMO, identificar
from config.settings import get_settings

BIENVENIDA = ("👋 Esto es una *demo* de un asistente de WhatsApp para estudios de pilates. "
              "Pruébalo como si fueras cliente: date de alta (te regalamos 5 sesiones de "
              "mentira) y reserva. Escribe *DEMO* para ver cómo probarlo como admin o "
              "instructor. Los datos se borran cada noche.")

AYUDA = ("🧪 *Comandos de la demo*\n"
         "• DEMO ADMIN: ves el menú de la dueña/o del estudio\n"
         "• DEMO INSTRUCTOR: ves el menú de un/a instructor/a\n"
         "• DEMO CLIENTE: vuelves a ser cliente\n"
         "• DEMO BASICO / DEMO IA: cambia el plan (menús o menús + IA)\n"
         "• MENÚ: muestra el menú\n"
         "Los pagos son simulados y los datos se borran cada noche.")


def activo() -> bool:
    return get_settings().demo


def regalar_bono(s: Session, ctx: Contexto) -> None:
    """RF-DEMO-001: 5 sesiones gratis al darse de alta en la demo."""
    if not activo() or ctx.cliente_id is None:
        return
    p = s.scalar(select(Producto).where(Producto.estudio_id == ctx.estudio_id,
                                        Producto.tipo == "bono").order_by(Producto.sesiones))
    if p is None:
        return
    hoy = ctx.ahora.date()
    s.add(Bono(estudio_id=ctx.estudio_id, cliente_id=ctx.cliente_id, producto_id=p.id,
               sesiones_total=5, sesiones_usadas=0, inicio=hoy,
               caduca=hoy + timedelta(days=30), estado=EstadoBono.ACTIVO))


def comando(s: Session, ctx: Contexto, clave: str) -> str | None:
    """RF-DEMO-002. `clave` ya normalizada ('demo admin'…). Devuelve el texto de respuesta,
    o None si no es un comando de demo."""
    if not activo() or not clave.startswith("demo"):
        return None
    orden = clave.removeprefix("demo").strip()
    if orden in ("admin", "instructor"):
        staff = s.scalar(select(Staff).where(Staff.estudio_id == ctx.estudio_id,
                                             Staff.telefono == ctx.telefono))
        rol = RolStaff.ADMIN if orden == "admin" else RolStaff.INSTRUCTOR
        if staff is None:
            s.add(Staff(estudio_id=ctx.estudio_id, telefono=ctx.telefono, rol=rol,
                        nombre=(ctx.nombre or "Visitante") + SUFIJO_DEMO))
        else:
            staff.rol, staff.activo = rol, True
        s.flush()
        _refrescar(s, ctx)
        return f"Ahora eres *{orden}* del estudio demo 🧪"
    if orden == "cliente":
        for st in s.scalars(select(Staff).where(Staff.estudio_id == ctx.estudio_id,
                                                Staff.telefono == ctx.telefono)):
            st.activo = False
        s.flush()
        _refrescar(s, ctx)
        if ctx.rol == Rol.LEAD:
            return "Ahora eres un contacto nuevo. Date de alta desde el menú para reservar."
        return "Vuelves a ser cliente 🙂"
    if orden in ("basico", "ia"):
        if orden == "ia" and not get_settings().anthropic_api_key:
            return "El plan IA no está disponible en esta demo (falta la API key)."
        estudio = s.get(Estudio, ctx.estudio_id)
        assert estudio is not None
        estudio.config = {**estudio.config,
                          "estudio": {**estudio.config.get("estudio", {}), "plan": orden}}
        return (f"Plan cambiado a *{'Básico (solo menús)' if orden == 'basico' else 'IA'}* "
                "para todo el estudio demo.")
    return AYUDA


def _refrescar(s: Session, ctx: Contexto) -> None:
    nuevo = identificar(s, ctx.estudio_id, ctx.telefono)
    ctx.rol, ctx.nombre = nuevo.rol, nuevo.nombre
    ctx.cliente_id, ctx.staff_id = nuevo.cliente_id, nuevo.staff_id
