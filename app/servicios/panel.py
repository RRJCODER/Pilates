"""Consultas que necesita el panel web del staff (RF-PAN-002…009).

Solo lectura y ayudas pequeñas: las acciones (reservar, cobrar, cancelar…) siguen
siendo los servicios de siempre, con sus `@requiere_rol` (RF-PAN-010).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.modelos import (
    AccionPendiente,
    Clase,
    Cliente,
    Conversacion,
    EstadoAccion,
    EstadoClase,
    EstadoCliente,
    EstadoConversacion,
    EstadoEspera,
    EstadoPago,
    EventoAgente,
    ListaEspera,
    Mensaje,
    Pago,
    Producto,
    Reserva,
    Staff,
    TipoClase,
)
from app.servicios.base import (
    Aviso,
    Contexto,
    ErrorNegocio,
    NoEncontrado,
    Rol,
    SinPermiso,
    a_utc,
    mostrar_telefono,
    requiere_rol,
)
from app.servicios.clases import DIAS, ocupadas, zona


def auditar(s: Session, ctx: Contexto, accion: str, datos: dict[str, Any] | None = None,
            resultado: dict[str, Any] | None = None) -> None:
    """RNF-SEG-005: cada acción del panel queda registrada con quién la hizo."""
    s.add(EventoAgente(agente="panel", herramienta=accion,
                       entrada={"staff_id": ctx.staff_id, **(datos or {})},
                       salida=resultado))


def _inicio_dia(d: date, ctx: Contexto, s: Session) -> datetime:
    return a_utc(datetime.combine(d, datetime.min.time(), zona(s, ctx.estudio_id)))


# ------------------------------------------------------------------ agenda


@requiere_rol(Rol.INSTRUCTOR)
def agenda_semana(s: Session, ctx: Contexto, lunes: date) -> list[dict[str, Any]]:
    """RF-PAN-002: los 7 días de la semana con sus clases. Un instructor ve solo las suyas."""
    tz = zona(s, ctx.estudio_id)
    q = (select(Clase).options(joinedload(Clase.tipo), joinedload(Clase.instructor))
         .where(Clase.estudio_id == ctx.estudio_id,
                Clase.inicio >= _inicio_dia(lunes, ctx, s),
                Clase.inicio < _inicio_dia(lunes + timedelta(days=7), ctx, s))
         .order_by(Clase.inicio))
    if ctx.rol == Rol.INSTRUCTOR:
        q = q.where(Clase.instructor_id == ctx.staff_id)
    dias: list[dict[str, Any]] = [
        {"fecha": lunes + timedelta(days=i),
         "nombre": f"{DIAS[i].capitalize()} {(lunes + timedelta(days=i)):%d/%m}",
         "clases": []} for i in range(7)]
    for c in s.scalars(q):
        inicio = a_utc(c.inicio).astimezone(tz)
        espera = s.scalar(select(func.count()).select_from(ListaEspera).where(
            ListaEspera.clase_id == c.id, ListaEspera.estado == EstadoEspera.ESPERANDO)) or 0
        dias[(inicio.date() - lunes).days]["clases"].append({
            "clase_id": c.id, "hora": inicio.strftime("%H:%M"), "clase": c.tipo.nombre,
            "modalidad": c.tipo.modalidad, "instructor": c.instructor.nombre,
            "ocupadas": ocupadas(s, c.id), "aforo": c.aforo, "espera": espera,
            "cancelada": c.estado == EstadoClase.CANCELADA,
            "pasada": a_utc(c.inicio) < ctx.ahora})
    return dias


@requiere_rol(Rol.ADMIN)
def opciones_clase(s: Session, ctx: Contexto) -> dict[str, Any]:
    """Tipos de clase e instructores para los formularios de RF-PAN-004."""
    tipos = s.scalars(select(TipoClase).where(TipoClase.estudio_id == ctx.estudio_id)
                      .order_by(TipoClase.nombre))
    staff = s.scalars(select(Staff).where(Staff.estudio_id == ctx.estudio_id,
                                          Staff.activo.is_(True)).order_by(Staff.nombre))
    return {"tipos": [{"clave": t.clave, "nombre": t.nombre, "aforo": t.aforo,
                       "duracion_min": t.duracion_min} for t in tipos],
            "instructores": [st.nombre for st in staff]}


@requiere_rol(Rol.ADMIN)
def datos_clase(s: Session, ctx: Contexto, clase_id: int) -> dict[str, Any]:
    c = s.get(Clase, clase_id)
    if c is None or c.estudio_id != ctx.estudio_id:
        raise NoEncontrado("No encuentro esa clase.")
    inicio = a_utc(c.inicio).astimezone(zona(s, ctx.estudio_id))
    return {"clase_id": c.id, "clase": c.tipo.nombre, "instructor": c.instructor.nombre,
            "aforo": c.aforo, "fecha": inicio.date().isoformat(),
            "hora": inicio.strftime("%H:%M"), "cancelada": c.estado == EstadoClase.CANCELADA}


# ------------------------------------------------------------------ clientes


@requiere_rol(Rol.INSTRUCTOR)
def historial_cliente(s: Session, ctx: Contexto, cliente_id: int) -> dict[str, Any]:
    """RF-PAN-005: reservas próximas y pasadas; los pagos solo los ve el admin."""
    cli = s.get(Cliente, cliente_id)
    if cli is None or cli.estudio_id != ctx.estudio_id or cli.estado == EstadoCliente.BORRADO:
        raise NoEncontrado("No encuentro ese cliente.")
    tz = zona(s, ctx.estudio_id)
    reservas = s.scalars(select(Reserva).join(Clase).options(joinedload(Reserva.clase))
                         .where(Reserva.cliente_id == cli.id).order_by(Clase.inicio.desc())
                         .limit(30))
    proximas: list[dict[str, Any]] = []
    pasadas: list[dict[str, Any]] = []
    for r in reservas:
        inicio = a_utc(r.clase.inicio).astimezone(tz)
        fila = {"reserva_id": r.id, "clase_id": r.clase_id, "clase": r.clase.tipo.nombre,
                "cuando": f"{DIAS[inicio.weekday()]} {inicio:%d/%m %H:%M}", "estado": r.estado}
        (proximas if a_utc(r.clase.inicio) >= ctx.ahora else pasadas).append(fila)
    out: dict[str, Any] = {"proximas": proximas[::-1], "pasadas": pasadas[:15], "pagos": None}
    if ctx.rol >= Rol.ADMIN:
        out["pagos"] = _pagos(s, select(Pago).where(Pago.cliente_id == cli.id), 20)
    return out


@requiere_rol(Rol.ADMIN)
def lista_clientes(s: Session, ctx: Contexto) -> list[dict[str, Any]]:
    """Para el desplegable de "registrar pago" (RF-PAN-006)."""
    q = select(Cliente).where(Cliente.estudio_id == ctx.estudio_id,
                              Cliente.estado == EstadoCliente.ACTIVO).order_by(Cliente.nombre)
    return [{"cliente_id": c.id, "nombre": c.nombre} for c in s.scalars(q)]


@requiere_rol(Rol.ADMIN)
def productos(s: Session, ctx: Contexto) -> list[dict[str, Any]]:
    q = select(Producto).where(Producto.estudio_id == ctx.estudio_id,
                               Producto.activo.is_(True)).order_by(Producto.precio)
    return [{"clave": p.clave, "nombre": p.nombre, "precio": float(p.precio)}
            for p in s.scalars(q)]


# ------------------------------------------------------------------ caja


def _pagos(s: Session, q: Any, limite: int) -> list[dict[str, Any]]:
    filas = []
    for p in s.scalars(q.order_by(func.coalesce(Pago.pagado_en, Pago.creado_en).desc())
                       .limit(limite)):
        cli = s.get(Cliente, p.cliente_id)
        prod = s.get(Producto, p.producto_id) if p.producto_id else None
        cuando = a_utc(p.pagado_en or p.creado_en)
        filas.append({"pago_id": p.id, "cliente_id": p.cliente_id,
                      "cliente": cli.nombre if cli else "—",
                      "producto": prod.nombre if prod else "—", "importe": float(p.importe),
                      "metodo": p.metodo, "estado": p.estado, "fecha": cuando.date().isoformat(),
                      "devolvible": p.estado == EstadoPago.PAGADO})
    return filas


@requiere_rol(Rol.ADMIN)
def pagos_periodo(s: Session, ctx: Contexto, desde: date, hasta: date) -> list[dict[str, Any]]:
    """`hasta` incluido, igual que `resumen_caja`."""
    cuando = func.coalesce(Pago.pagado_en, Pago.creado_en)
    q = select(Pago).where(Pago.estudio_id == ctx.estudio_id,
                           cuando >= _inicio_dia(desde, ctx, s),
                           cuando < _inicio_dia(hasta + timedelta(days=1), ctx, s))
    return _pagos(s, q, 200)


# ------------------------------------------------------------------ confirmaciones


@requiere_rol(Rol.INSTRUCTOR)
def ver_accion(s: Session, ctx: Contexto, codigo: str) -> dict[str, Any]:
    """La pantalla de confirmación del panel (diseño §7 y §12)."""
    a = s.get(AccionPendiente, codigo.strip().upper())
    if a is None or a.estudio_id != ctx.estudio_id or a.quien_confirma != ctx.telefono:
        raise NoEncontrado("No encuentro esa acción.")
    caducada = a.estado == EstadoAccion.PENDIENTE and a_utc(a.expira_en) < ctx.ahora
    return {"codigo": a.codigo, "tipo": a.tipo, "descripcion": a.descripcion,
            "estado": EstadoAccion.EXPIRADA if caducada else a.estado,
            "minutos": max(int((a_utc(a.expira_en) - ctx.ahora).total_seconds() // 60), 0)}


# ------------------------------------------------------------------ conversaciones


def _conversacion(s: Session, ctx: Contexto, conversacion_id: int) -> Conversacion:
    conv = s.get(Conversacion, conversacion_id)
    if conv is None or conv.estudio_id != ctx.estudio_id:
        raise NoEncontrado("No encuentro esa conversación.")
    return conv


def _nombre(s: Session, conv: Conversacion) -> str:
    if conv.cliente_id:
        cli = s.get(Cliente, conv.cliente_id)
        if cli:
            return cli.nombre
    st = s.scalar(select(Staff).where(Staff.estudio_id == conv.estudio_id,
                                      Staff.telefono == conv.telefono))
    return st.nombre if st else "Contacto sin ficha"


@requiere_rol(Rol.ADMIN)
def conversaciones_panel(s: Session, ctx: Contexto, limite: int = 30) -> dict[str, Any]:
    """RF-PAN-008: primero las derivadas a una persona, luego las más recientes."""
    q = (select(Conversacion).where(Conversacion.estudio_id == ctx.estudio_id,
                                    ~Conversacion.telefono.like("borrado-%"))
         .order_by(Conversacion.ultima_entrada_en.desc().nulls_last()).limit(limite))
    derivadas: list[dict[str, Any]] = []
    recientes: list[dict[str, Any]] = []
    for conv in s.scalars(q):
        humano = (conv.estado == EstadoConversacion.HUMANO
                  and (conv.humano_hasta is None or a_utc(conv.humano_hasta) >= ctx.ahora))
        ultimo = s.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv.id)
                           .order_by(Mensaje.id.desc())).first()
        fila = {"conversacion_id": conv.id, "nombre": _nombre(s, conv),
                "telefono": mostrar_telefono(conv.telefono), "humano": humano,
                "ultimo": (ultimo.contenido[:90] if ultimo else ""),
                "cuando": a_utc(conv.ultima_entrada_en).astimezone(zona(s, ctx.estudio_id))
                .strftime("%d/%m %H:%M") if conv.ultima_entrada_en else ""}
        (derivadas if humano else recientes).append(fila)
    return {"derivadas": derivadas, "recientes": recientes}


@requiere_rol(Rol.ADMIN)
def ver_conversacion(s: Session, ctx: Contexto, conversacion_id: int) -> dict[str, Any]:
    conv = _conversacion(s, ctx, conversacion_id)
    tz = zona(s, ctx.estudio_id)
    mensajes = list(s.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv.id)
                              .order_by(Mensaje.id.desc()).limit(60)))[::-1]
    humano = (conv.estado == EstadoConversacion.HUMANO
              and (conv.humano_hasta is None or a_utc(conv.humano_hasta) >= ctx.ahora))
    return {"conversacion_id": conv.id, "nombre": _nombre(s, conv),
            "telefono": mostrar_telefono(conv.telefono), "cliente_id": conv.cliente_id,
            "humano": humano,
            "humano_hasta": a_utc(conv.humano_hasta).astimezone(tz).strftime("%H:%M")
            if humano and conv.humano_hasta else None,
            "mensajes": [{"rol": m.rol, "texto": m.contenido,
                          "hora": a_utc(m.creado_en).astimezone(tz).strftime("%d/%m %H:%M")
                          if m.creado_en else ""} for m in mensajes]}


@requiere_rol(Rol.ADMIN)
def responder_conversacion(s: Session, ctx: Contexto, conversacion_id: int,
                           texto: str) -> dict[str, Any]:
    """RF-PAN-008: equivale a `responder +34… mensaje` (RF-ORQ-004)."""
    from config.settings import get_settings

    if get_settings().demo:  # RF-PAN-011 / RF-DEMO-006
        raise SinPermiso("En la demo no se puede escribir a otros números.")
    if not texto.strip():
        raise ErrorNegocio("Escribe el mensaje antes de enviarlo.")
    conv = _conversacion(s, ctx, conversacion_id)
    ctx.avisos.append(Aviso(conv.telefono, texto.strip()))
    return {"resultado": "ok"}


@requiere_rol(Rol.ADMIN)
def reactivar_conversacion(s: Session, ctx: Contexto, conversacion_id: int) -> dict[str, Any]:
    """RF-PAN-008: equivale a `reactivar +34…`."""
    conv = _conversacion(s, ctx, conversacion_id)
    conv.estado, conv.humano_hasta = EstadoConversacion.BOT, None
    return {"resultado": "ok"}

