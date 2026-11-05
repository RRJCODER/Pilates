"""Marketing y comunicación (RF-MKT-001..007).

Los envíos del cron (recordatorios, reseñas, cumpleaños) no pasan por el LLM:
son plantillas deterministas. Solo las campañas las redacta el agente, y el
admin las aprueba antes de enviarlas.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import extract, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.modelos import (
    Bono,
    Campana,
    Clase,
    Cliente,
    EnvioProgramado,
    EstadoBono,
    EstadoCampana,
    EstadoClase,
    EstadoCliente,
    EstadoPago,
    EstadoReserva,
    Pago,
    Reserva,
    TipoClase,
)
from app.servicios.acciones import crear_accion, ejecutor
from app.servicios.base import (
    Aviso,
    Contexto,
    ErrorNegocio,
    NoEncontrado,
    Rol,
    a_utc,
    datos_estudio,
    requiere_rol,
)
from app.servicios.clases import ocupadas, zona
from app.servicios.clientes import telefonos_admin


def _una_vez(s: Session, estudio_id: int, clave: str) -> bool:
    """True la primera vez que se pide una clave (idempotencia del cron)."""
    try:
        with s.begin_nested():
            s.add(EnvioProgramado(estudio_id=estudio_id, clave=clave))
            s.flush()
        return True
    except IntegrityError:
        return False


def _ultima_asistencia(s: Session, cliente_id: int) -> datetime | None:
    return s.scalar(select(func.max(Clase.inicio)).join(Reserva).where(
        Reserva.cliente_id == cliente_id,
        Reserva.estado.in_([EstadoReserva.ASISTIO, EstadoReserva.CONFIRMADA])))


@requiere_rol(Rol.ADMIN)
def clientes_inactivos(s: Session, ctx: Contexto, dias: int = 21) -> list[dict[str, Any]]:
    """RF-MKT-002."""
    limite = ctx.ahora - timedelta(days=dias)
    out = []
    for c in s.scalars(select(Cliente).where(Cliente.estudio_id == ctx.estudio_id,
                                             Cliente.estado == EstadoCliente.ACTIVO)):
        ultima = _ultima_asistencia(s, c.id)
        if ultima is None or a_utc(ultima) < limite:
            out.append({"cliente_id": c.id, "nombre": c.nombre, "telefono": c.telefono,
                        "ultima_clase": a_utc(ultima).date().isoformat() if ultima else None,
                        "consent_marketing": c.consent_marketing})
    return out


@requiere_rol(Rol.ADMIN)
def segmentar(s: Session, ctx: Contexto, nivel: str | None = None,
              modalidad: str | None = None, con_bono_activo: bool | None = None,
              inactivos_dias: int | None = None) -> dict[str, Any]:
    """RF-MKT-004/005. Solo devuelve clientes con consentimiento de marketing."""
    q = select(Cliente).where(Cliente.estudio_id == ctx.estudio_id,
                              Cliente.estado == EstadoCliente.ACTIVO,
                              Cliente.consent_marketing.is_(True))
    if nivel:
        q = q.where(Cliente.nivel == nivel.lower())
    ids = {c.id for c in s.scalars(q)}
    if modalidad:
        ids &= set(s.scalars(select(Reserva.cliente_id).join(Clase).join(TipoClase).where(
            TipoClase.modalidad == modalidad.lower())))
    if con_bono_activo is not None:
        activos = set(s.scalars(select(Bono.cliente_id).where(
            Bono.estado == EstadoBono.ACTIVO, Bono.estudio_id == ctx.estudio_id)))
        ids = ids & activos if con_bono_activo else ids - activos
    if inactivos_dias:
        ids &= {c["cliente_id"] for c in clientes_inactivos(s, ctx, inactivos_dias)}
    filtro = {"nivel": nivel, "modalidad": modalidad, "con_bono_activo": con_bono_activo,
              "inactivos_dias": inactivos_dias}
    return {"filtro": filtro, "clientes": sorted(ids), "total": len(ids)}


@requiere_rol(Rol.ADMIN)
def borrador_campana(s: Session, ctx: Contexto, texto: str, nivel: str | None = None,
                     modalidad: str | None = None, con_bono_activo: bool | None = None,
                     inactivos_dias: int | None = None) -> dict[str, Any]:
    seg = segmentar(s, ctx, nivel, modalidad, con_bono_activo, inactivos_dias)
    camp = Campana(estudio_id=ctx.estudio_id, segmento=seg, texto=texto,
                   plantilla="campana_generica")
    s.add(camp)
    s.flush()
    return {"resultado": "ok", "campana_id": camp.id, "destinatarios": seg["total"],
            "texto": texto, "siguiente_paso": "Llama a enviar_campana para pedir aprobación."}


@requiere_rol(Rol.ADMIN)
def enviar_campana(s: Session, ctx: Contexto, campana_id: int) -> dict[str, Any]:
    camp = s.get(Campana, campana_id)
    if camp is None or camp.estudio_id != ctx.estudio_id:
        raise NoEncontrado("No encuentro esa campaña.")
    if camp.estado != EstadoCampana.BORRADOR:
        raise ErrorNegocio(f"La campaña está {camp.estado}.")
    return crear_accion(s, ctx, "enviar_campana",
                        f"Enviar la campaña {camp.id} a {camp.segmento['total']} clientes:\n"
                        f"«{camp.texto}»", {"campana_id": camp.id})


@ejecutor("enviar_campana")
def _ejecutar_campana(s: Session, ctx: Contexto, p: dict[str, Any]) -> dict[str, Any]:
    camp = s.get(Campana, int(p["campana_id"]))
    assert camp is not None
    enviados = 0
    for cid in camp.segmento["clientes"]:
        cli = s.get(Cliente, cid)
        # Se vuelve a comprobar el consentimiento en el momento del envío (RF-MKT-005)
        if cli is None or not cli.consent_marketing or cli.estado != EstadoCliente.ACTIVO:
            continue
        ctx.avisos.append(Aviso(cli.telefono, camp.texto + "\n\n(Responde BAJA para no "
                                                           "recibir más promociones)",
                                plantilla="campana_generica",
                                parametros=[cli.nombre.split()[0], camp.texto]))
        enviados += 1
    camp.estado, camp.enviada_en, camp.aprobada_por = EstadoCampana.ENVIADA, ctx.ahora, ctx.staff_id
    return {"enviados": enviados}


@requiere_rol(Rol.ADMIN)
def informe_semanal(s: Session, ctx: Contexto, fin: date | None = None) -> dict[str, Any]:
    """RF-MKT-007: ocupación por franja, altas, ingresos y clientes en riesgo."""
    fin = fin or ctx.ahora.date()
    ini = fin - timedelta(days=7)
    tz = zona(s, ctx.estudio_id)
    clases = list(s.scalars(select(Clase).where(
        Clase.estudio_id == ctx.estudio_id, Clase.estado == EstadoClase.PROGRAMADA,
        Clase.inicio >= datetime.combine(ini, datetime.min.time(), tz),
        Clase.inicio < datetime.combine(fin, datetime.min.time(), tz))))
    franjas: dict[str, list[float]] = {}
    for c in clases:
        h = a_utc(c.inicio).astimezone(tz).hour
        franja = "mañana" if h < 14 else "tarde" if h < 19 else "noche"
        franjas.setdefault(franja, []).append(ocupadas(s, c.id) / c.aforo)
    ocupacion = {f: round(100 * sum(v) / len(v)) for f, v in franjas.items()}
    total = round(100 * sum(sum(v) for v in franjas.values()) / max(len(clases), 1))
    altas = s.scalar(select(func.count()).select_from(Cliente).where(
        Cliente.estudio_id == ctx.estudio_id,
        Cliente.creado_en >= datetime.combine(ini, datetime.min.time(), tz))) or 0
    ingresos = s.scalar(select(func.sum(Pago.importe)).where(
        Pago.estudio_id == ctx.estudio_id, Pago.estado == EstadoPago.PAGADO,
        Pago.pagado_en >= datetime.combine(ini, datetime.min.time(), tz))) or 0
    riesgo = clientes_inactivos(s, ctx, 14)
    return {"semana": f"{ini.isoformat()} – {fin.isoformat()}", "clases": len(clases),
            "ocupacion_media_pct": total, "ocupacion_por_franja_pct": ocupacion,
            "altas": altas, "ingresos_eur": float(ingresos),
            "clientes_en_riesgo": len(riesgo), "en_riesgo": [c["nombre"] for c in riesgo[:10]]}


# ------------------------------------------------------------------ cron


def recordatorios_clase(s: Session, ctx: Contexto, horas_antes: list[int],
                        ventana_min: int = 15) -> int:
    """RF-MKT-001. Se ejecuta cada `ventana_min` minutos."""
    n = 0
    tz = zona(s, ctx.estudio_id)
    for h in horas_antes:
        desde = ctx.ahora + timedelta(hours=h)
        hasta = desde + timedelta(minutes=ventana_min)
        q = (select(Reserva, Clase).join(Clase).where(
            Clase.estudio_id == ctx.estudio_id, Clase.estado == EstadoClase.PROGRAMADA,
            Clase.inicio >= desde, Clase.inicio < hasta,
            Reserva.estado == EstadoReserva.CONFIRMADA))
        for r, c in s.execute(q).all():
            if not _una_vez(s, ctx.estudio_id, f"recordatorio:{r.id}:{h}"):
                continue
            cli = s.get(Cliente, r.cliente_id)
            if cli is None:
                continue
            ini = a_utc(c.inicio).astimezone(tz)
            cuando = "mañana" if h >= 12 else "hoy"
            ctx.avisos.append(Aviso(
                cli.telefono,
                f"⏰ Te recordamos tu clase de {c.tipo.nombre} {cuando} a las {ini:%H:%M} con "
                f"{c.instructor.nombre}. Si no puedes venir, avísanos y liberamos la plaza.",
                plantilla="recordatorio_clase",
                parametros=[cli.nombre.split()[0], c.tipo.nombre, cuando, f"{ini:%H:%M}",
                            c.instructor.nombre]))
            n += 1
    return n


def pedir_resenas(s: Session, ctx: Contexto, tras_clases: int) -> int:
    """RF-MKT-003: una sola vez, tras la N-ésima clase asistida."""
    url = datos_estudio(s, ctx.estudio_id).get("google_review_url")
    if not url:
        return 0
    n = 0
    filas = s.execute(select(Reserva.cliente_id, func.count()).where(
        Reserva.estudio_id == ctx.estudio_id, Reserva.estado == EstadoReserva.ASISTIO)
        .group_by(Reserva.cliente_id)
        .having(func.count() >= tras_clases)).all()
    for cid, _ in filas:
        if not _una_vez(s, ctx.estudio_id, f"resena:{cid}"):
            continue
        cli = s.get(Cliente, cid)
        if cli is None or cli.estado != EstadoCliente.ACTIVO:
            continue
        ctx.avisos.append(Aviso(cli.telefono,
                                f"¡Gracias por venir estas semanas, {cli.nombre.split()[0]}! 💛 "
                                f"¿Nos dejas una reseña? Nos ayuda muchísimo: {url}",
                                plantilla="resena", parametros=[cli.nombre.split()[0], url]))
        n += 1
    return n


def felicitar_cumpleanos(s: Session, ctx: Contexto, hoy: date) -> int:
    """RF-MKT-006: solo con consentimiento de marketing."""
    n = 0
    for cli in s.scalars(select(Cliente).where(
            Cliente.estudio_id == ctx.estudio_id, Cliente.estado == EstadoCliente.ACTIVO,
            Cliente.consent_marketing.is_(True), Cliente.fecha_nac.is_not(None),
            extract("month", Cliente.fecha_nac) == hoy.month,
            extract("day", Cliente.fecha_nac) == hoy.day)):
        if not _una_vez(s, ctx.estudio_id, f"cumple:{cli.id}:{hoy.year}"):
            continue
        ctx.avisos.append(Aviso(cli.telefono,
                                f"🎂 ¡Feliz cumpleaños, {cli.nombre.split()[0]}! Todo el equipo "
                                "te desea un día genial.",
                                plantilla="cumpleanos", parametros=[cli.nombre.split()[0]]))
        n += 1
    return n


def proponer_recuperaciones(s: Session, ctx: Contexto, dias: int, hoy: date) -> int:
    """RF-MKT-002: avisa al admin de los inactivos nuevos (una vez por semana)."""
    clave = f"inactivos:{hoy.isocalendar().year}-{hoy.isocalendar().week}"
    if not _una_vez(s, ctx.estudio_id, clave):
        return 0
    inactivos = clientes_inactivos(s, ctx, dias)
    if not inactivos:
        return 0
    nombres = ", ".join(c["nombre"] for c in inactivos[:15])
    for tel in telefonos_admin(s, ctx.estudio_id):
        ctx.avisos.append(Aviso(tel, f"👀 {len(inactivos)} clientes llevan más de {dias} días "
                                     f"sin venir: {nombres}. ¿Quieres que prepare un mensaje "
                                     "para recuperarlos?"))
    return len(inactivos)


def enviar_informe_semanal(s: Session, ctx: Contexto, hoy: date) -> dict[str, Any]:
    inf = informe_semanal(s, ctx, hoy)
    franjas = ", ".join(f"{k} {v}%" for k, v in inf["ocupacion_por_franja_pct"].items())
    texto = (f"📊 Informe semanal ({inf['semana']})\n"
             f"• Clases: {inf['clases']} · Ocupación media: {inf['ocupacion_media_pct']}% "
             f"({franjas})\n• Altas: {inf['altas']}\n• Ingresos: {inf['ingresos_eur']:.2f} €\n"
             f"• En riesgo (14+ días sin venir): {inf['clientes_en_riesgo']}")
    for tel in telefonos_admin(s, ctx.estudio_id):
        ctx.avisos.append(Aviso(tel, texto, plantilla="informe_semanal", parametros=[texto]))
    return inf
