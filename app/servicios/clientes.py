"""Clientes: identificación, alta, ficha, lesiones, notas y RGPD.

RF-CLI-*, RF-MKT-005, RF-MEM-005.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.modelos import (
    Bono,
    Clase,
    Cliente,
    Conversacion,
    EstadoCliente,
    EstadoReserva,
    Lesion,
    ListaEspera,
    Memoria,
    Mensaje,
    NotaInterna,
    Reserva,
    Resumen,
    RolStaff,
    Staff,
)
from app.servicios.acciones import crear_accion, ejecutor
from app.servicios.base import (
    Aviso,
    Contexto,
    ErrorNegocio,
    NoEncontrado,
    Rol,
    mostrar_telefono,
    requiere_rol,
)
from app.servicios.bonos import describir_bono


def identificar(s: Session, estudio_id: int, telefono: str) -> Contexto:
    """RF-ORQ-001: el rol sale de la base de datos, nunca del mensaje."""
    staff = s.scalar(select(Staff).where(Staff.estudio_id == estudio_id,
                                         Staff.telefono == telefono, Staff.activo.is_(True)))
    if staff:
        rol = Rol.ADMIN if staff.rol == RolStaff.ADMIN else Rol.INSTRUCTOR
        return Contexto(estudio_id=estudio_id, telefono=telefono, rol=rol, nombre=staff.nombre,
                        staff_id=staff.id)
    cli = s.scalar(select(Cliente).where(Cliente.estudio_id == estudio_id,
                                         Cliente.telefono == telefono,
                                         Cliente.estado != EstadoCliente.BORRADO))
    if cli and cli.estado != EstadoCliente.LEAD:
        return Contexto(estudio_id=estudio_id, telefono=telefono, rol=Rol.CLIENTE,
                        nombre=cli.nombre, cliente_id=cli.id)
    return Contexto(estudio_id=estudio_id, telefono=telefono, rol=Rol.LEAD,
                    nombre=cli.nombre if cli else None, cliente_id=cli.id if cli else None)


SUFIJO_DEMO = " (demo)"


def telefonos_admin(s: Session, estudio_id: int) -> list[str]:
    q = select(Staff.telefono).where(
        Staff.estudio_id == estudio_id, Staff.rol == RolStaff.ADMIN, Staff.activo.is_(True))
    # RF-DEMO-006: los visitantes que se hacen admin en la demo no reciben avisos ajenos
    q = q.where(Staff.nombre.not_like(f"%{SUFIJO_DEMO}"))
    return list(s.scalars(q))


@requiere_rol(Rol.LEAD)
def alta_cliente(s: Session, ctx: Contexto, nombre: str, acepta_privacidad: bool,
                 email: str | None = None, nivel: str | None = None,
                 objetivos: str | None = None, como_nos_conocio: str | None = None,
                 consent_salud: bool = False, consent_marketing: bool = False,
                 fecha_nacimiento: date | None = None) -> dict[str, Any]:
    """RF-CLI-001. Los consentimientos se piden por separado (constitución §3)."""
    if ctx.rol != Rol.LEAD:
        raise ErrorNegocio("Ya estás dado de alta.")
    if not acepta_privacidad:
        raise ErrorNegocio("Para darte de alta necesito que aceptes la política de privacidad.")
    cli = s.scalar(select(Cliente).where(Cliente.estudio_id == ctx.estudio_id,
                                         Cliente.telefono == ctx.telefono))
    if cli is None:
        cli = Cliente(estudio_id=ctx.estudio_id, telefono=ctx.telefono, nombre=nombre)
        s.add(cli)
    cli.nombre, cli.email, cli.nivel = nombre, email, (nivel or "inicial").lower()
    cli.objetivos, cli.como_nos_conocio = objetivos, como_nos_conocio
    cli.fecha_nac = fecha_nacimiento
    cli.consent_salud, cli.consent_marketing = consent_salud, consent_marketing
    cli.consent_fecha, cli.estado = ctx.ahora, EstadoCliente.ACTIVO
    s.flush()
    ctx.rol, ctx.cliente_id, ctx.nombre = Rol.CLIENTE, cli.id, cli.nombre
    from app.menu import demo  # RF-DEMO-001 (no hace nada fuera de la demo)

    demo.regalar_bono(s, ctx)
    for tel in telefonos_admin(s, ctx.estudio_id):
        ctx.avisos.append(Aviso(tel, f"🆕 Nuevo cliente: {nombre} "
                                     f"({mostrar_telefono(ctx.telefono)}). "
                                     f"Nivel {cli.nivel}. Objetivos: {objetivos or '-'}"))
    return {"resultado": "ok", "cliente_id": cli.id}


@requiere_rol(Rol.CLIENTE)
def actualizar_consentimientos(s: Session, ctx: Contexto, consent_salud: bool | None = None,
                               consent_marketing: bool | None = None) -> dict[str, Any]:
    cli = _cliente(s, ctx, ctx.exigir_cliente())
    if consent_salud is not None:
        cli.consent_salud = consent_salud
        if not consent_salud:  # sin consentimiento no guardamos datos de salud
            s.execute(delete(Lesion).where(Lesion.cliente_id == cli.id))
    if consent_marketing is not None:
        cli.consent_marketing = consent_marketing
    cli.consent_fecha = ctx.ahora
    return {"resultado": "ok", "consent_salud": cli.consent_salud,
            "consent_marketing": cli.consent_marketing}


@requiere_rol(Rol.CLIENTE)
def darse_de_baja_promos(s: Session, ctx: Contexto) -> dict[str, Any]:
    """RF-MKT-005: 'BAJA' desactiva todas las promociones."""
    return actualizar_consentimientos(s, ctx, consent_marketing=False)


@requiere_rol(Rol.CLIENTE)
def registrar_lesion(s: Session, ctx: Contexto, descripcion: str) -> dict[str, Any]:
    """RF-CLI-004. Solo con consentimiento de salud."""
    cli = _cliente(s, ctx, ctx.exigir_cliente())
    if not cli.consent_salud:
        return {"resultado": "requiere_consentimiento",
                "mensaje": "Para guardar información de salud necesito tu consentimiento "
                           "explícito. Solo la verán tus instructores."}
    s.add(Lesion(estudio_id=ctx.estudio_id, cliente_id=cli.id, descripcion=descripcion))
    proximas = s.scalars(select(Clase).join(Reserva).where(
        Reserva.cliente_id == cli.id, Reserva.estado == EstadoReserva.CONFIRMADA,
        Clase.inicio > ctx.ahora))
    for tel in {c.instructor.telefono for c in proximas}:
        ctx.avisos.append(Aviso(tel, f"🩹 {cli.nombre} ha comunicado: {descripcion}. "
                                     "Viene a alguna de tus próximas clases."))
    return {"resultado": "ok"}


@requiere_rol(Rol.CLIENTE)
def dar_lesion_por_resuelta(s: Session, ctx: Contexto) -> dict[str, Any]:
    cid = ctx.exigir_cliente()
    n = 0
    for lz in s.scalars(select(Lesion).where(Lesion.cliente_id == cid, Lesion.activa.is_(True))):
        lz.activa = False
        n += 1
    return {"resultado": "ok", "lesiones_cerradas": n}


@requiere_rol(Rol.INSTRUCTOR)
def buscar_cliente(s: Session, ctx: Contexto, texto: str) -> list[dict[str, Any]]:
    patron = f"%{texto.lower()}%"
    q = select(Cliente).where(Cliente.estudio_id == ctx.estudio_id,
                              Cliente.estado != EstadoCliente.BORRADO,
                              or_(func.lower(Cliente.nombre).like(patron),
                                  Cliente.telefono.like(patron),
                                  func.lower(func.coalesce(Cliente.email, "")).like(patron)))
    return [{"cliente_id": c.id, "nombre": c.nombre, "telefono": mostrar_telefono(c.telefono),
             "nivel": c.nivel}
            for c in s.scalars(q.limit(10))]


@requiere_rol(Rol.INSTRUCTOR)
def ficha_cliente(s: Session, ctx: Contexto, cliente_id: int) -> dict[str, Any]:
    c = _cliente(s, ctx, cliente_id)
    bonos = s.scalars(select(Bono).where(Bono.cliente_id == c.id).order_by(Bono.caduca.desc())
                      .limit(5))
    asistencias = s.scalar(select(func.count()).select_from(Reserva).where(
        Reserva.cliente_id == c.id, Reserva.estado == EstadoReserva.ASISTIO)) or 0
    notas = s.scalars(select(NotaInterna).where(NotaInterna.cliente_id == c.id)
                      .order_by(NotaInterna.creado_en.desc()).limit(10))
    lesiones = s.scalars(select(Lesion).where(Lesion.cliente_id == c.id,
                                              Lesion.activa.is_(True)))
    return {"cliente_id": c.id, "nombre": c.nombre, "telefono": mostrar_telefono(c.telefono),
            "email": c.email,
            "nivel": c.nivel, "objetivos": c.objetivos, "estado": c.estado,
            "consent_marketing": c.consent_marketing, "asistencias": asistencias,
            "bonos": [describir_bono(b) for b in bonos],
            "lesiones": [lz.descripcion for lz in lesiones],
            "notas_internas": [n.texto for n in notas]}


@requiere_rol(Rol.INSTRUCTOR)
def nota_interna(s: Session, ctx: Contexto, cliente_id: int, texto: str) -> dict[str, Any]:
    _cliente(s, ctx, cliente_id)
    s.add(NotaInterna(estudio_id=ctx.estudio_id, cliente_id=cliente_id, staff_id=ctx.staff_id,
                      texto=texto))
    return {"resultado": "ok"}


@requiere_rol(Rol.CLIENTE)
def mi_ficha(s: Session, ctx: Contexto) -> dict[str, Any]:
    c = _cliente(s, ctx, ctx.exigir_cliente())
    return {"nombre": c.nombre, "email": c.email, "nivel": c.nivel, "objetivos": c.objetivos,
            "consent_salud": c.consent_salud, "consent_marketing": c.consent_marketing}


@requiere_rol(Rol.CLIENTE)
def borrar_mis_datos(s: Session, ctx: Contexto) -> dict[str, Any]:
    ctx.exigir_cliente()
    return crear_accion(s, ctx, "borrar_datos",
                        "Borrar tu ficha, tu historial de conversación y tu memoria. "
                        "Se cancelarán tus reservas futuras. Los pagos se conservan "
                        "anonimizados por obligación legal.", {"cliente_id": ctx.cliente_id})


@ejecutor("borrar_datos")
def _ejecutar_borrado(s: Session, ctx: Contexto, p: dict[str, Any]) -> dict[str, Any]:
    """RF-MEM-005: derecho de supresión."""
    c = _cliente(s, ctx, int(p["cliente_id"]))
    for r in s.scalars(select(Reserva).join(Clase).where(
            Reserva.cliente_id == c.id, Reserva.estado == EstadoReserva.CONFIRMADA,
            Clase.inicio > ctx.ahora)):
        r.estado, r.cancelada_en = EstadoReserva.CANCELADA, ctx.ahora
    s.execute(delete(ListaEspera).where(ListaEspera.cliente_id == c.id))
    s.execute(delete(Lesion).where(Lesion.cliente_id == c.id))
    s.execute(delete(NotaInterna).where(NotaInterna.cliente_id == c.id))
    s.execute(delete(Memoria).where(Memoria.cliente_id == c.id))
    for conv in s.scalars(select(Conversacion).where(Conversacion.estudio_id == ctx.estudio_id,
                                                     Conversacion.telefono == c.telefono)):
        s.execute(delete(Mensaje).where(Mensaje.conversacion_id == conv.id))
        s.execute(delete(Resumen).where(Resumen.conversacion_id == conv.id))
        conv.cliente_id, conv.telefono = None, f"borrado-{c.id}"
    c.nombre, c.email, c.telefono, c.objetivos = "Cliente borrado", None, f"borrado-{c.id}", None
    c.fecha_nac, c.consent_salud, c.consent_marketing = None, False, False
    c.estado = EstadoCliente.BORRADO
    return {"borrado": True}


def _cliente(s: Session, ctx: Contexto, cliente_id: int) -> Cliente:
    c = s.get(Cliente, cliente_id)
    if c is None or c.estudio_id != ctx.estudio_id or c.estado == EstadoCliente.BORRADO:
        raise NoEncontrado("No encuentro ese cliente.")
    return c
