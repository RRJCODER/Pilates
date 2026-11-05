"""Motor de menús y botones: plan Básico y pulsaciones del plan IA (diseño §10).

No llama a la IA. Usa los mismos servicios (y permisos) que los subagentes.
Los ids de las opciones llevan la acción y sus datos, así que no hace falta
guardar estado salvo cuando un flujo espera texto (`conversaciones.flujo`).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.modelos import (
    AccionPendiente,
    AmbitoMemoria,
    Clase,
    Conversacion,
    EstadoAccion,
    EstadoEspera,
    ListaEspera,
    Memoria,
)
from app.integraciones.canal import Canal, Fila
from app.servicios import (
    acciones,
    bonos,
    clases,
    clientes,
    conversaciones,
    lista_espera,
    marketing,
    pagos,
    reservas,
)
from app.servicios.base import (
    Aviso,
    Contexto,
    ErrorNegocio,
    Rol,
    a_utc,
    datos_estudio,
    politicas,
)

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
DIAS_CORTOS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


# ------------------------------------------------------------------ salidas


@dataclass
class Texto:
    texto: str


@dataclass
class Botones:
    texto: str
    opciones: list[tuple[str, str]]


@dataclass
class Lista:
    texto: str
    etiqueta: str
    filas: list[Fila] = field(default_factory=list)


Salida = Texto | Botones | Lista
MENU = ("menu", "🏠 Menú")


def enviar(canal: Canal, telefono: str, salidas: list[Salida]) -> None:
    for sal in salidas:
        if isinstance(sal, Texto):
            canal.enviar_texto(telefono, sal.texto)
        elif isinstance(sal, Botones):
            canal.enviar_botones(telefono, sal.texto, sal.opciones)
        else:
            canal.enviar_lista(telefono, sal.texto, sal.etiqueta, sal.filas)


def como_texto(salidas: list[Salida]) -> str:
    """Versión de texto para el historial (y para la IA del plan IA)."""
    partes = []
    for sal in salidas:
        if isinstance(sal, Texto):
            partes.append(sal.texto)
        elif isinstance(sal, Botones):
            partes.append(f"{sal.texto}\n[botones: {' | '.join(t for _, t in sal.opciones)}]")
        else:
            partes.append(f"{sal.texto}\n[opciones: {' | '.join(f.titulo for f in sal.filas)}]")
    return "\n\n".join(partes)


# ------------------------------------------------------------------ enrutado


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9+ ]", "", sin_tildes.lower()).strip()


PALABRAS_CLAVE = {"menu", "baja", "si", "no"}
RE_STAFF = re.compile(r"^(reactivar|responder)\s+(\+?\d{6,15})\s*(.*)$", re.I | re.S)


def plan_estudio(s: Session, estudio_id: int) -> str:
    return str(datos_estudio(s, estudio_id).get("plan", "ia")).lower()


def debe_manejar(s: Session, plan: str, conv: Conversacion, texto: str,
                 respuesta_id: str | None, ctx: Contexto) -> bool:
    """RF-PLAN-002/003: ¿lo resuelve el menú (sin IA)?"""
    if respuesta_id or conv.flujo or plan == "basico":
        return True
    if ctx.rol == Rol.ADMIN and RE_STAFF.match(texto.strip()):
        return True
    clave = normalizar(texto)
    if clave in ("si", "no"):
        # En el plan IA, un "sí" puede contestar a una pregunta de la IA: solo lo
        # resuelve el menú si hay una plaza ofrecida o una acción pendiente.
        return _hay_algo_que_confirmar(s, ctx)
    return clave in PALABRAS_CLAVE


def _hay_algo_que_confirmar(s: Session, ctx: Contexto) -> bool:
    if _accion_pendiente(s, ctx) is not None:
        return True
    return ctx.cliente_id is not None and s.scalars(select(ListaEspera.id).where(
        ListaEspera.cliente_id == ctx.cliente_id, ListaEspera.estado == EstadoEspera.OFRECIDA,
        ListaEspera.expira_en > ctx.ahora)).first() is not None


def manejar(s: Session, ctx: Contexto, conv: Conversacion, texto: str,
            respuesta_id: str | None) -> list[Salida]:
    """Punto de entrada del motor. Cada acción va en un savepoint: si falla, no deja
    cambios a medias y se explica el error con el botón de menú."""
    try:
        with s.begin_nested():
            return _despachar(s, ctx, conv, texto, respuesta_id)
    except ErrorNegocio as e:
        return [Botones(f"⚠️ {e}", [MENU])]


def _despachar(s: Session, ctx: Contexto, conv: Conversacion, texto: str,
               rid: str | None) -> list[Salida]:
    if rid:
        if not rid.startswith("alta:"):
            conv.flujo = None
        return _accion(s, ctx, conv, rid)

    texto = texto.strip()
    if conv.flujo:
        return _texto_en_flujo(s, ctx, conv, texto)

    m = RE_STAFF.match(texto)
    if m and ctx.rol == Rol.ADMIN:
        return _comando_admin(s, ctx, m.group(1).lower(), m.group(2), m.group(3))

    clave = normalizar(texto)
    if clave == "menu":
        return menu_principal(s, ctx)
    if clave == "baja":
        if ctx.rol < Rol.CLIENTE:
            return [Texto("No tienes promociones activas 🙂")]
        clientes.darse_de_baja_promos(s, ctx)
        return [Botones("Hecho: ya no recibirás promociones. Seguirás recibiendo los avisos "
                        "de tus clases.", [MENU])]
    if clave == "si":
        return _decir_si(s, ctx)
    if clave == "no":
        pend = _accion_pendiente(s, ctx)
        if pend:
            acciones.rechazar_accion(s, ctx, pend.codigo)
            return [Botones("De acuerdo, no hago nada.", [MENU])]
        return menu_principal(s, ctx)
    # Texto libre en el plan Básico (RF-BAS-005)
    saludo = clave in {"hola", "buenas", "buenos dias", "buenas tardes", "hey", "ola"}
    salidas: list[Salida] = []
    if not saludo:
        salidas.append(Texto("Te atiendo con este menú 👇 (si necesitas algo que no está, "
                             "elige «Hablar con el estudio»)."))
    return salidas + menu_principal(s, ctx)


# ------------------------------------------------------------------ menú principal


def menu_principal(s: Session, ctx: Contexto) -> list[Salida]:
    """RF-BAS-001: opciones según el rol."""
    nombre = datos_estudio(s, ctx.estudio_id)["nombre"]
    hola = f"¡Hola{', ' + ctx.nombre.split()[0] if ctx.nombre else ''}! 👋"
    if ctx.rol == Rol.LEAD:
        filas = [Fila("info", "ℹ️ Información", "Dónde estamos, normas, primera clase"),
                 Fila("precios", "💶 Precios", "Bonos, mensualidades y clase suelta"),
                 Fila("alta", "📝 Darme de alta", "Para reservar tu clase de prueba"),
                 Fila("humano", "💬 Hablar con el estudio")]
        return [Lista(f"{hola} Bienvenida/o a {nombre}. ¿Qué te apetece?", "Ver opciones",
                      filas)]
    filas = []
    if ctx.rol == Rol.CLIENTE:
        filas = [Fila("r:dias", "📅 Reservar clase"),
                 Fila("mis", "🗓️ Mis reservas", "Ver o cancelar"),
                 Fila("bono", "🎟️ Mi bono", "Sesiones y caducidad"),
                 Fila("comprar", "💳 Comprar bono"),
                 Fila("info", "ℹ️ Info del estudio"),
                 Fila("humano", "💬 Hablar con el estudio")]
    else:
        filas = [Fila("st:clases:0", "📋 Mis clases de hoy", "Asistentes y asistencia"),
                 Fila("st:clases:1", "📋 Mis clases de mañana")]
        if ctx.rol == Rol.ADMIN:
            filas[0].titulo, filas[1].titulo = "📋 Clases de hoy", "📋 Clases de mañana"
            filas += [Fila("ad:cancelar", "🚫 Cancelar una clase"),
                      Fila("ad:caja", "💶 Caja de hoy"),
                      Fila("ad:informe", "📊 Informe semanal"),
                      Fila("ad:inactivos", "👀 Clientes inactivos")]
    return [Lista(f"{hola} ¿Qué quieres hacer?", "Ver opciones", filas)]


# ------------------------------------------------------------------ acciones


def _accion(s: Session, ctx: Contexto, conv: Conversacion, rid: str) -> list[Salida]:
    partes = rid.split(":")
    cabeza = partes[0]
    sencillas: dict[str, Callable[[Session, Contexto], list[Salida]]] = {
        "menu": menu_principal, "info": _info, "precios": _precios, "bono": _bono,
        "comprar": _comprar, "mis": _mis_reservas, "humano": _humano,
    }
    if rid in sencillas:
        return sencillas[rid](s, ctx)
    if rid == "alta":
        if ctx.rol != Rol.LEAD:
            return menu_principal(s, ctx)
        conv.flujo = {"paso": "alta_nombre"}
        return [Texto("¡Genial! 😊 ¿Cómo te llamas? Escríbeme tu nombre y apellido.")]
    if cabeza == "alta":
        return _alta(s, ctx, conv, partes)
    if cabeza == "r":
        if partes[1] == "dias":
            return _dias(s, ctx)
        if partes[1] == "dia":
            return _clases_dia(s, ctx, date.fromisoformat(partes[2]))
        if partes[1] == "clase":
            return _reservar(s, ctx, int(partes[2]))
    if cabeza == "le":
        r = lista_espera.unirse(s, ctx, int(partes[1]))
        delante = r["personas_delante"]
        return [Botones(f"Te he apuntado a la lista de espera de {r['clase']} ({r['dia']}, "
                        f"{r['hora']}). Tienes {delante} persona(s) delante. Si se libera "
                        "una plaza te escribo 🙌", [MENU])]
    if cabeza == "lok":
        return _aceptar_plaza(s, ctx, int(partes[1]))
    if cabeza == "c":
        return _confirmar_cancelacion(s, ctx, int(partes[1]))
    if cabeza == "cx":
        r = reservas.cancelar_reserva(s, ctx, int(partes[1]))
        c = r["cancelada"]
        extra = "Te he devuelto la sesión al bono." if r["sesion_devuelta"] else (
            "Como era con poca antelación, la sesión no se devuelve.")
        return [Botones(f"Cancelada: {c['clase']} del {c['dia']} a las {c['hora']}. {extra}",
                        [("r:dias", "📅 Reservar otra"), MENU])]
    if cabeza == "p":
        r = pagos.crear_enlace_pago(s, ctx, partes[1])
        return [Texto(f"Aquí tienes el enlace para pagar *{r['producto']}* "
                      f"({r['importe_eur']:.2f} €):\n{r['enlace_pago']}\n\nEn cuanto se "
                      "confirme el pago te lo activo y te aviso ✅")]
    if cabeza == "ok":
        res = acciones.confirmar_accion(s, ctx, partes[1])
        return [Botones(f"✅ Hecho: {res['accion']}", [MENU])]
    if cabeza == "no":
        acciones.rechazar_accion(s, ctx, partes[1])
        return [Botones("De acuerdo, no hago nada.", [MENU])]
    if cabeza == "st":
        return _staff(s, ctx, partes)
    if cabeza == "ad":
        return _admin(s, ctx, partes)
    return menu_principal(s, ctx)


def _tz(s: Session, ctx: Contexto) -> ZoneInfo:
    return clases.zona(s, ctx.estudio_id)


def _info(s: Session, ctx: Contexto) -> list[Salida]:
    d = datos_estudio(s, ctx.estudio_id)
    lineas = [f"*{d['nombre']}*"]
    lineas += [v for v in (d.get("direccion"), d.get("web")) if v]
    for m in s.scalars(select(Memoria).where(Memoria.estudio_id == ctx.estudio_id,
                                             Memoria.ambito == AmbitoMemoria.ESTUDIO)
                       .order_by(Memoria.ruta)):
        lineas.append(m.contenido)
    pol = politicas(s, ctx.estudio_id)
    lineas.append(f"Cancelaciones: con al menos {pol['cancelacion_horas_limite']} h de "
                  "antelación se devuelve la sesión.")
    siguiente = ("alta", "📝 Darme de alta") if ctx.rol == Rol.LEAD else ("r:dias", "📅 Reservar")
    return [Texto("\n\n".join(lineas)), Botones("¿Algo más?", [siguiente, MENU])]


def _precios(s: Session, ctx: Contexto) -> list[Salida]:
    lineas = [f"• {p['nombre']}: {p['precio_eur']:.2f} € ({p['sesiones']} sesión/es, "
              f"válido {p['validez_dias']} días)" for p in bonos.catalogo(s, ctx)]
    siguiente = ("alta", "📝 Darme de alta") if ctx.rol == Rol.LEAD else ("comprar", "💳 Comprar")
    return [Botones("💶 Precios\n" + "\n".join(lineas), [siguiente, MENU])]


def _bono(s: Session, ctx: Contexto) -> list[Salida]:
    eb = bonos.estado_bono(s, ctx)
    if not eb["bonos"]:
        return [Botones("Ahora mismo no tienes ningún bono activo.",
                        [("comprar", "💳 Comprar bono"), MENU])]
    lineas = []
    for b in eb["bonos"]:
        caduca = date.fromisoformat(b["caduca"]).strftime("%d/%m/%Y") if b["caduca"] else "-"
        estado = {"activo": "", "congelado": " (congelado)",
                  "pendiente_pago": " (pendiente de pago)"}.get(b["estado"], "")
        lineas.append(f"• {b['producto']}{estado}: te quedan *{b['sesiones_restantes']}* de "
                      f"{b['sesiones_total']}, hasta el {caduca}")
    return [Botones("🎟️ Tus bonos\n" + "\n".join(lineas),
                    [("r:dias", "📅 Reservar"), ("comprar", "💳 Comprar bono"), MENU])]


def _comprar(s: Session, ctx: Contexto) -> list[Salida]:
    filas = [Fila(f"p:{p['producto']}", p["nombre"],
                  f"{p['precio_eur']:.2f} € · {p['sesiones']} ses. · {p['validez_dias']} días")
             for p in bonos.catalogo(s, ctx)]
    return [Lista("¿Qué quieres comprar?", "Ver catálogo", filas)]


def _humano(s: Session, ctx: Contexto) -> list[Salida]:
    conversaciones.derivar_a_humano(s, ctx, "Pide hablar con el estudio desde el menú",
                                    urgente=False)
    return [Texto("He avisado al equipo del estudio; te escribirán por aquí en cuanto puedan "
                  "💛 Mientras tanto, cuéntales lo que necesites en este chat.")]


# ------------------------------------------------------------------ reservar


def _dias(s: Session, ctx: Contexto) -> list[Salida]:
    tz = _tz(s, ctx)
    hoy = ctx.ahora.astimezone(tz).date()
    fin = datetime.combine(hoy + timedelta(days=7), time(), tz)
    todas = clases.buscar_clases(s, ctx, ctx.ahora, fin, solo_con_plazas=True, limite=200)
    por_dia: dict[str, int] = {}
    for c in todas:
        por_dia[c["fecha"]] = por_dia.get(c["fecha"], 0) + 1
    filas = []
    for d in range(7):
        dia = hoy + timedelta(days=d)
        n = por_dia.get(dia.isoformat(), 0)
        if n:
            nombre = "Hoy" if d == 0 else "Mañana" if d == 1 else DIAS[dia.weekday()].capitalize()
            filas.append(Fila(f"r:dia:{dia.isoformat()}", f"{nombre} {dia:%d/%m}",
                              f"{n} clase{'s' if n > 1 else ''} con plazas"))
    if not filas:
        return [Botones("No quedan plazas libres en los próximos 7 días 😕", [MENU])]
    return [Lista("📅 ¿Qué día quieres venir?", "Elegir día", filas)]


def _clases_dia(s: Session, ctx: Contexto, dia: date) -> list[Salida]:
    tz = _tz(s, ctx)
    ini = datetime.combine(dia, time(), tz)
    lista = clases.buscar_clases(s, ctx, ini, ini + timedelta(days=1), limite=10)
    if not lista:
        return [Botones("Ese día ya no quedan clases.", [("r:dias", "📅 Otro día"), MENU])]
    filas = [Fila(f"r:clase:{c['clase_id']}", f"{c['hora']} {c['clase']}",
                  f"{c['instructor']} · " + (f"{c['plazas_libres']} plaza(s)"
                                             if c["plazas_libres"] else "Completa"))
             for c in lista]
    return [Lista(f"Clases del {DIAS[dia.weekday()]} {dia:%d/%m}:", "Elegir clase", filas)]


def _reservar(s: Session, ctx: Contexto, clase_id: int) -> list[Salida]:
    r = reservas.reservar_clase(s, ctx, clase_id)
    if r["resultado"] == "clase_llena":
        alt = "\n".join(f"• {a['dia']} {a['hora']} {a['clase']} ({a['instructor']})"
                        for a in r["alternativas"])
        texto = "Esa clase está completa 😕"
        if alt:
            texto += f"\nAlternativas con plaza:\n{alt}"
        return [Botones(texto, [(f"le:{clase_id}", "⏳ Lista de espera"),
                                ("r:dias", "📅 Ver otros días"), MENU])]
    return [Botones(f"✅ ¡Reservado! {r['clase']} el {r['dia']} a las {r['hora']} con "
                    f"{r['instructor']}. Te quedan {r['sesiones_restantes_bono']} sesiones.",
                    [("r:dias", "📅 Reservar otra"), MENU])]


def _aceptar_plaza(s: Session, ctx: Contexto, clase_id: int) -> list[Salida]:
    r = reservas.aceptar_plaza_lista_espera(s, ctx, clase_id)
    return [Botones(f"✅ ¡Plaza tuya! {r['clase']} el {r['dia']} a las {r['hora']} con "
                    f"{r['instructor']}.", [MENU])]


def _mis_reservas(s: Session, ctx: Contexto) -> list[Salida]:
    lista = reservas.mis_reservas(s, ctx)
    if not lista:
        return [Botones("No tienes reservas próximas.", [("r:dias", "📅 Reservar"), MENU])]
    filas = [Fila(f"c:{r['reserva_id']}", f"{r['dia'].capitalize()} {r['hora']}",
                  f"{r['clase']} · {r['instructor']}") for r in lista[:10]]
    return [Lista("🗓️ Tus próximas clases. Elige una si quieres cancelarla:", "Ver reservas",
                  filas)]


def _confirmar_cancelacion(s: Session, ctx: Contexto, reserva_id: int) -> list[Salida]:
    """RF-BAS-003: avisa de la política antes de cancelar."""
    propia = {r["reserva_id"]: r for r in reservas.mis_reservas(s, ctx)}.get(reserva_id)
    if propia is None:
        raise ErrorNegocio("No encuentro esa reserva entre las tuyas.")
    clase = s.get(Clase, propia["clase_id"])
    assert clase is not None
    pol = politicas(s, ctx.estudio_id)
    horas = (a_utc(clase.inicio) - ctx.ahora).total_seconds() / 3600
    if horas >= float(pol["cancelacion_horas_limite"]) or (
            pol["cancelacion_tardia"] == "sin_penalizacion"):
        aviso = "Se te devolverá la sesión al bono."
    else:
        aviso = (f"⚠️ Faltan menos de {pol['cancelacion_horas_limite']} h, así que la sesión "
                 "*no* se devuelve.")
    return [Botones(f"¿Cancelar {propia['clase']} del {propia['dia']} a las "
                    f"{propia['hora']}? {aviso}",
                    [(f"cx:{reserva_id}", "✅ Sí, cancelar"), ("mis", "❌ No")])]


# ------------------------------------------------------------------ alta (RF-BAS-004)


TEXTO_PRIVACIDAD = ("Para darte de alta guardaremos tu nombre, teléfono, reservas y pagos para "
                    "gestionar tus clases. Puedes pedir que borremos tus datos cuando quieras. "
                    "¿Aceptas la política de privacidad?")


def _texto_en_flujo(s: Session, ctx: Contexto, conv: Conversacion, texto: str) -> list[Salida]:
    flujo = conv.flujo or {}
    if normalizar(texto) == "menu":
        conv.flujo = None
        return menu_principal(s, ctx)
    if flujo.get("paso") == "alta_nombre":
        nombre = " ".join(texto.split())[:120]
        if len(nombre) < 2 or not re.search(r"[a-zA-ZÀ-ÿ]", nombre):
            return [Texto("No lo he entendido 😅 Escríbeme tu nombre y apellido.")]
        conv.flujo = {"paso": "alta_privacidad", "nombre": nombre}
        return [Botones(f"Encantada/o, {nombre.split()[0]}. {TEXTO_PRIVACIDAD}",
                        [("alta:priv:si", "✅ Acepto"), ("alta:priv:no", "❌ No acepto")])]
    # Esperábamos un botón y ha escrito texto: repetimos la pregunta
    if flujo.get("paso") == "alta_privacidad":
        return [Botones(TEXTO_PRIVACIDAD,
                        [("alta:priv:si", "✅ Acepto"), ("alta:priv:no", "❌ No acepto")])]
    if flujo.get("paso") == "alta_marketing":
        return [_pregunta_marketing()]
    conv.flujo = None
    return menu_principal(s, ctx)


def _pregunta_marketing() -> Botones:
    return Botones("¿Quieres recibir por aquí promociones y novedades del estudio? "
                   "(Podrás darte de baja cuando quieras escribiendo BAJA)",
                   [("alta:mkt:si", "Sí, quiero"), ("alta:mkt:no", "No, gracias")])


def _alta(s: Session, ctx: Contexto, conv: Conversacion, partes: list[str]) -> list[Salida]:
    flujo = conv.flujo or {}
    if ctx.rol != Rol.LEAD or "nombre" not in flujo:
        conv.flujo = None
        return menu_principal(s, ctx)
    if partes[1] == "priv":
        if partes[2] != "si":
            conv.flujo = None
            return [Botones("Sin aceptar la política no puedo darte de alta. Si cambias de "
                            "idea, vuelve a elegir «Darme de alta».", [MENU])]
        conv.flujo = {**flujo, "paso": "alta_marketing"}
        return [_pregunta_marketing()]
    if partes[1] == "mkt":
        clientes.alta_cliente(s, ctx, flujo["nombre"], acepta_privacidad=True,
                              consent_marketing=partes[2] == "si")
        conv.flujo = None
        return [Texto(f"🎉 ¡Ya estás dada/o de alta, {flujo['nombre'].split()[0]}! Para "
                      "reservar necesitas una clase de prueba o un bono.")] + _comprar(s, ctx)
    return menu_principal(s, ctx)


# ------------------------------------------------------------------ SÍ / NO


def _accion_pendiente(s: Session, ctx: Contexto) -> AccionPendiente | None:
    return s.scalars(select(AccionPendiente).where(
        AccionPendiente.estudio_id == ctx.estudio_id,
        AccionPendiente.quien_confirma == ctx.telefono,
        AccionPendiente.estado == EstadoAccion.PENDIENTE,
        AccionPendiente.expira_en > ctx.ahora).order_by(AccionPendiente.creado_en.desc())
    ).first()


def _decir_si(s: Session, ctx: Contexto) -> list[Salida]:
    """RF-BAS-005: SÍ acepta una plaza ofrecida o confirma la acción pendiente."""
    if ctx.cliente_id is not None:
        oferta = s.scalars(select(ListaEspera).where(
            ListaEspera.cliente_id == ctx.cliente_id,
            ListaEspera.estado == EstadoEspera.OFRECIDA,
            ListaEspera.expira_en > ctx.ahora)).first()
        if oferta:
            return _aceptar_plaza(s, ctx, oferta.clase_id)
    pend = _accion_pendiente(s, ctx)
    if pend:
        res = acciones.confirmar_accion(s, ctx, pend.codigo)
        return [Botones(f"✅ Hecho: {res['accion']}", [MENU])]
    return menu_principal(s, ctx)


def botones_confirmacion(info: dict[str, Any]) -> Botones:
    return Botones(f"{info['descripcion']}\n¿Lo confirmas?",
                   [(f"ok:{info['codigo']}", "✅ Confirmar"), (f"no:{info['codigo']}",
                                                               "❌ Cancelar")])


# ------------------------------------------------------------------ staff


def _staff(s: Session, ctx: Contexto, partes: list[str]) -> list[Salida]:
    if partes[1] == "clases":
        tz = _tz(s, ctx)
        dia = ctx.ahora.astimezone(tz).date() + timedelta(days=int(partes[2]))
        ini = datetime.combine(dia, time(), tz).astimezone(UTC)
        lista = clases.clases_de_instructor(s, ctx, ini, ini + timedelta(days=1))
        if not lista:
            return [Botones(f"No hay clases el {DIAS[dia.weekday()]} {dia:%d/%m}.", [MENU])]
        ocup = {c["clase_id"]: c["plazas_libres"] for c in lista}
        filas = [Fila(f"st:clase:{c['clase_id']}", f"{c['hora']} {c['clase']}",
                      f"{c['instructor']} · {ocup[c['clase_id']]} libre(s)") for c in lista[:10]]
        return [Lista(f"📋 Clases del {DIAS[dia.weekday()]} {dia:%d/%m}:", "Ver clases", filas)]
    if partes[1] == "clase":
        info = clases.asistentes_clase(s, ctx, int(partes[2]))
        if not info["asistentes"]:
            return [Botones(f"{info['clase']} {info['hora']}: todavía no hay nadie apuntado.",
                            [MENU])]
        filas = []
        for a in info["asistentes"]:
            marca = {"asistio": "✅", "falta": "❌"}.get(a["estado"], "•")
            desc = ("🩹 " + "; ".join(a["lesiones"])) if a["lesiones"] else (a["nivel"] or "")
            filas.append(Fila(f"st:asis:{a['reserva_id']}", f"{marca} {a['cliente']}", desc))
        return [Lista(f"{info['clase']} {info['dia']} {info['hora']} · "
                      f"{len(info['asistentes'])} apuntados. Toca a alguien para marcar "
                      "asistencia:", "Asistentes", filas)]
    if partes[1] == "asis":
        rid = partes[2]
        return [Botones("¿Ha venido?", [(f"st:as:{rid}:1", "✅ Vino"),
                                        (f"st:as:{rid}:0", "❌ Faltó"), MENU])]
    if partes[1] == "as":
        r = clases.marcar_asistencia(s, ctx, int(partes[2]), partes[3] == "1")
        from app.db.modelos import Reserva

        reserva = s.get(Reserva, r["reserva_id"])
        volver = (f"st:clase:{reserva.clase_id}", "↩️ Volver a la clase") if reserva else MENU
        return [Botones("Anotado ✅", [volver, MENU])]
    return menu_principal(s, ctx)


def _admin(s: Session, ctx: Contexto, partes: list[str]) -> list[Salida]:
    if ctx.rol < Rol.ADMIN:
        raise ErrorNegocio("Esa opción es solo para el admin.")
    if partes[1] == "cancelar":
        tz = _tz(s, ctx)
        lista = clases.clases_de_instructor(s, ctx, ctx.ahora, ctx.ahora + timedelta(days=3))
        if not lista:
            return [Botones("No hay clases en los próximos 3 días.", [MENU])]
        filas = [Fila(f"ad:cc:{c['clase_id']}",
                      f"{DIAS_CORTOS[date.fromisoformat(c['fecha']).weekday()]} {c['hora']} "
                      f"{c['clase']}", c["instructor"]) for c in lista[:10]]
        del tz
        return [Lista("¿Qué clase quieres cancelar? (se avisa a los apuntados y recuperan la "
                      "sesión)", "Elegir clase", filas)]
    if partes[1] == "cc":
        info = clases.cancelar_clase(s, ctx, int(partes[2]), "cancelada por el estudio")
        return [botones_confirmacion(info)]
    if partes[1] == "caja":
        hoy = ctx.ahora.astimezone(_tz(s, ctx)).date()
        c = pagos.resumen_caja(s, ctx, hoy, hoy)
        return [Botones(f"💶 Caja de hoy\nCobrado: {c['cobrado_eur']:.2f} €\nDevuelto: "
                        f"{c['devuelto_eur']:.2f} €\nPendiente: {c['pendiente_eur']:.2f} €",
                        [MENU])]
    if partes[1] == "informe":
        i = marketing.informe_semanal(s, ctx)
        franjas = ", ".join(f"{k} {v}%" for k, v in i["ocupacion_por_franja_pct"].items())
        return [Botones(f"📊 {i['semana']}\nClases: {i['clases']} · Ocupación "
                        f"{i['ocupacion_media_pct']}% ({franjas})\nAltas: {i['altas']}\n"
                        f"Ingresos: {i['ingresos_eur']:.2f} €\nEn riesgo: "
                        f"{i['clientes_en_riesgo']}", [MENU])]
    if partes[1] == "inactivos":
        dias = int(politicas(s, ctx.estudio_id)["inactividad_dias"])
        ina = marketing.clientes_inactivos(s, ctx, dias)
        lineas = "\n".join(f"• {c['nombre']} ({c['telefono']}) · última: "
                           f"{c['ultima_clase'] or 'nunca'}" for c in ina[:20])
        return [Botones(f"👀 {len(ina)} clientes sin venir en {dias}+ días\n{lineas}"
                        if ina else "Nadie lleva tanto tiempo sin venir 🎉", [MENU])]
    return menu_principal(s, ctx)


def _comando_admin(s: Session, ctx: Contexto, orden: str, tel: str,
                   resto: str) -> list[Salida]:
    """El admin responde o devuelve el control al bot (RF-ORQ-004)."""
    tel = tel if tel.startswith("+") else "+" + tel
    if orden == "reactivar":
        r = conversaciones.reactivar_bot(s, ctx, tel)
        return [Texto("Listo, el asistente vuelve a atender a ese número." if r["resultado"]
                      == "ok" else "No encuentro conversación con ese número.")]
    if not resto.strip():
        return [Texto("Escribe: responder +34… tu mensaje")]
    ctx.avisos.append(Aviso(tel, resto.strip()))
    return [Texto(f"Enviado a {tel} ✅")]
