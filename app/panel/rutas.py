"""Panel web del staff (RF-PAN-*, diseño §12).

Las páginas se renderizan en el servidor con Jinja2 y usan formularios normales
(POST y redirección). Todas las acciones pasan por los servicios de siempre, con
un `Contexto` del staff que ha iniciado sesión (RF-PAN-010).
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy.orm import Session

from app.conversacion import entregar, procesar_mensaje
from app.db.modelos import Staff
from app.db.sesion import sesion
from app.integraciones.canal import get_canal
from app.panel import acceso
from app.panel.simulador import CanalWeb
from app.servicios import clases, clientes, marketing, pagos, panel
from app.servicios.acciones import confirmar_accion, rechazar_accion
from app.servicios.base import Contexto, ErrorNegocio, Rol, SinPermiso, datos_estudio
from app.servicios.clases import zona
from config.settings import get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/panel")
simulador = APIRouter(prefix="/dev")
DIR = Path(__file__).parent
plantillas = Jinja2Templates(directory=DIR / "plantillas")


def _whatsapp(texto: str) -> Markup:
    """Formato de WhatsApp (*negrita*, _cursiva_) sobre el texto ya escapado."""
    seguro = str(escape(texto))
    seguro = re.sub(r"\*([^*\n]+)\*", r"<strong>\1</strong>", seguro)
    seguro = re.sub(r"(?<![\w/])_([^_\n]+)_(?!\w)", r"<em>\1</em>", seguro)
    return Markup(seguro)


plantillas.env.filters["whatsapp"] = _whatsapp
COOKIE = "panel_sesion"


def ahora() -> datetime:
    """Reloj del panel (los tests lo sustituyen)."""
    return datetime.now(UTC)


def _estudio_id() -> int:
    return get_settings().estudio_id


# ------------------------------------------------------------------ sesión y contexto


class SinSesion(Exception):
    pass


def staff_actual(request: Request) -> Contexto:
    token = request.cookies.get(COOKIE)
    with sesion() as s:
        st = acceso.leer_sesion(s, token, ahora())
        if st is None:
            raise SinSesion
        ctx = clientes.identificar(s, _estudio_id(), st.telefono)
    if ctx.rol < Rol.INSTRUCTOR:
        raise SinSesion
    ctx.ahora = ahora()
    return ctx


def _comprobar_csrf(request: Request, enviado: str) -> None:
    if not acceso.csrf_valido(request.cookies.get(COOKIE), enviado):
        raise HTTPException(400, "El formulario ha caducado. Vuelve a cargar la página.")


def _pagina(request: Request, nombre: str, ctx: Contexto | None, **datos: Any) -> HTMLResponse:
    estudio = "Estudio"
    with sesion() as s, contextlib.suppress(ErrorNegocio):
        estudio = datos_estudio(s, _estudio_id())["nombre"]
    token = request.cookies.get(COOKIE)
    return plantillas.TemplateResponse(request, nombre, {
        "ctx": ctx, "es_admin": bool(ctx and ctx.rol >= Rol.ADMIN), "estudio": estudio,
        "csrf": acceso.token_csrf(token) if token else "",
        "demo": get_settings().demo, "consola": get_settings().canal == "consola",
        "ok": request.query_params.get("ok"), "error": request.query_params.get("error"),
        **datos})


def _volver(url: str, ok: str | None = None, error: str | None = None) -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if ok:
        url += f"{sep}ok={quote(ok)}"
    elif error:
        url += f"{sep}error={quote(error)}"
    return RedirectResponse(url, status_code=303)


def _accion(ctx: Contexto, nombre: str, datos: dict[str, Any],
            fn: Callable[[Session], dict[str, Any]]) -> dict[str, Any]:
    """Ejecuta un servicio en una transacción, lo audita y entrega los avisos después."""
    with sesion() as s:
        res = fn(s)
        panel.auditar(s, ctx, nombre, datos, {k: v for k, v in res.items()
                                             if isinstance(v, str | int | float | bool)})
    if ctx.avisos:
        entregar(ctx.estudio_id, list(ctx.avisos))
        ctx.avisos.clear()
    return res


def _tras_accion(res: dict[str, Any], volver: str, ok: str) -> RedirectResponse:
    """Si el servicio ha creado una acción pendiente, se va a la pantalla de confirmar."""
    if res.get("resultado") == "requiere_confirmacion":
        return RedirectResponse(f"/panel/confirmar/{res['codigo']}?volver={quote(volver)}",
                                status_code=303)
    return _volver(volver, ok=ok)


def _lunes(ctx: Contexto, s: Session, semana: str | None) -> date:
    if semana:
        try:
            d = date.fromisoformat(semana)
        except ValueError:
            d = ctx.ahora.astimezone(zona(s, ctx.estudio_id)).date()
    else:
        d = ctx.ahora.astimezone(zona(s, ctx.estudio_id)).date()
    return d - timedelta(days=d.weekday())


# ------------------------------------------------------------------ acceso (RF-PAN-001)


@router.get("/entrar", response_class=HTMLResponse)
def entrar(request: Request) -> HTMLResponse:
    return _pagina(request, "entrar.html", None)


@router.post("/entrar")
def pedir_codigo(telefono: str = Form("")) -> RedirectResponse:
    tel = acceso.normalizar_telefono(telefono)
    with sesion() as s:
        enviado = acceso.pedir_codigo(s, _estudio_id(), tel, ahora())
    log.info("Código del panel para %s: %s", tel, "enviado" if enviado else "no enviado")
    # Misma respuesta tanto si es staff como si no (no revela quién trabaja en el estudio)
    return RedirectResponse(f"/panel/codigo?tel={quote(tel)}", status_code=303)


@router.get("/codigo", response_class=HTMLResponse)
def pantalla_codigo(request: Request, tel: str = "") -> HTMLResponse:
    return _pagina(request, "codigo.html", None, tel=tel)


@router.post("/codigo")
def comprobar_codigo(telefono: str = Form(""), codigo: str = Form("")) -> Response:
    tel = acceso.normalizar_telefono(telefono)
    with sesion() as s:
        token = acceso.validar_codigo(s, _estudio_id(), tel, codigo, ahora())
    if token is None:
        return _volver(f"/panel/codigo?tel={quote(tel)}",
                       error="Código incorrecto o caducado. Revísalo o pide uno nuevo.")
    resp = RedirectResponse("/panel", status_code=303)
    resp.set_cookie(COOKIE, token, max_age=get_settings().panel_sesion_horas * 3600,
                    httponly=True, samesite="strict", path="/",
                    secure=get_settings().base_url_publica.startswith("https://"))
    return resp


@router.post("/salir")
def salir(request: Request, csrf: str = Form("")) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    with sesion() as s:
        acceso.cerrar_sesion(s, request.cookies.get(COOKIE))
    resp = RedirectResponse("/panel/entrar", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


# ------------------------------------------------------------------ agenda (RF-PAN-002/003)


@router.get("", response_class=HTMLResponse)
def agenda(request: Request, semana: str | None = None,
           ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        lunes = _lunes(ctx, s, semana)
        dias = panel.agenda_semana(s, ctx, lunes)
        hoy = ctx.ahora.astimezone(zona(s, ctx.estudio_id)).date()
    return _pagina(request, "agenda.html", ctx, dias=dias, lunes=lunes, hoy=hoy,
                   anterior=(lunes - timedelta(days=7)).isoformat(),
                   siguiente=(lunes + timedelta(days=7)).isoformat())


@router.get("/clases/nueva", response_class=HTMLResponse)
def nueva_clase(request: Request, fecha: str = "",
                ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        opciones = panel.opciones_clase(s, ctx)
    return _pagina(request, "clase_form.html", ctx, opciones=opciones, clase=None,
                   fecha=fecha or ctx.ahora.date().isoformat())


@router.post("/clases/nueva")
def crear_clase(request: Request, tipo: str = Form(...), fecha: str = Form(...),
                hora: str = Form(...), instructor: str = Form(...), aforo: str = Form(""),
                csrf: str = Form(""), ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    try:
        inicio = datetime.combine(date.fromisoformat(fecha), time.fromisoformat(hora))
        res = _accion(ctx, "crear_clase", {"tipo": tipo, "inicio": inicio.isoformat()},
                      lambda s: clases.crear_clase(s, ctx, tipo, inicio, instructor,
                                                   int(aforo) if aforo else None))
    except (ErrorNegocio, ValueError) as e:
        return _volver(f"/panel/clases/nueva?fecha={fecha}", error=str(e))
    return _volver(f"/panel/clases/{res['clase_id']}", ok="Clase creada.")


@router.get("/clases/{clase_id}", response_class=HTMLResponse)
def ver_clase(request: Request, clase_id: int,
              ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        datos = clases.asistentes_clase(s, ctx, clase_id)
        extra = panel.datos_clase(s, ctx, clase_id) if ctx.rol >= Rol.ADMIN else None
    return _pagina(request, "clase.html", ctx, c=datos, extra=extra)


@router.post("/reservas/{reserva_id}/asistencia")
def asistencia(request: Request, reserva_id: int, clase_id: int = Form(...),
               asistio: str = Form(...), csrf: str = Form(""),
               ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    try:
        _accion(ctx, "marcar_asistencia", {"reserva_id": reserva_id, "asistio": asistio},
                lambda s: clases.marcar_asistencia(s, ctx, reserva_id, asistio == "1"))
    except SinPermiso:
        raise
    except ErrorNegocio as e:
        return _volver(f"/panel/clases/{clase_id}", error=str(e))
    return _volver(f"/panel/clases/{clase_id}")


@router.get("/clases/{clase_id}/editar", response_class=HTMLResponse)
def editar_clase(request: Request, clase_id: int,
                 ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        opciones = panel.opciones_clase(s, ctx)
        datos = panel.datos_clase(s, ctx, clase_id)
    return _pagina(request, "clase_form.html", ctx, opciones=opciones, clase=datos,
                   fecha=datos["fecha"])


@router.post("/clases/{clase_id}/editar")
def guardar_clase(request: Request, clase_id: int, fecha: str = Form(...),
                  hora: str = Form(...), instructor: str = Form(...), aforo: str = Form(""),
                  csrf: str = Form(""), ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    volver = f"/panel/clases/{clase_id}"
    try:
        with sesion() as s:
            antes = panel.datos_clase(s, ctx, clase_id)
        inicio = datetime.combine(date.fromisoformat(fecha), time.fromisoformat(hora))
        cambia_hora = (fecha, hora) != (antes["fecha"], antes["hora"])
        _accion(ctx, "modificar_clase", {"clase_id": clase_id},
                lambda s: clases.modificar_clase(
                    s, ctx, clase_id, inicio=inicio if cambia_hora else None,
                    instructor=instructor if instructor != antes["instructor"] else None,
                    aforo=int(aforo) if aforo else None))
    except SinPermiso:
        raise
    except (ErrorNegocio, ValueError) as e:
        return _volver(f"{volver}/editar", error=str(e))
    return _volver(volver, ok="Clase actualizada. Si ha cambiado la hora o el instructor, "
                              "se ha avisado a las personas apuntadas.")


@router.post("/clases/{clase_id}/cancelar")
def cancelar_clase(request: Request, clase_id: int, motivo: str = Form(""),
                   csrf: str = Form(""), ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    volver = f"/panel/clases/{clase_id}"
    try:
        res = _accion(ctx, "cancelar_clase", {"clase_id": clase_id},
                      lambda s: clases.cancelar_clase(s, ctx, clase_id,
                                                      motivo.strip() or "imprevisto del estudio"))
    except SinPermiso:
        raise
    except ErrorNegocio as e:
        return _volver(volver, error=str(e))
    return _tras_accion(res, "/panel", "Clase cancelada.")


# ------------------------------------------------------------------ confirmaciones (§7)


@router.get("/confirmar/{codigo}", response_class=HTMLResponse)
def pantalla_confirmar(request: Request, codigo: str, volver: str = "/panel",
                       ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        accion = panel.ver_accion(s, ctx, codigo)
    return _pagina(request, "confirmar.html", ctx, accion=accion, volver=_seguro(volver))


def _seguro(url: str) -> str:
    """Solo se vuelve a páginas del panel (evita redirecciones abiertas)."""
    return url if url.startswith("/panel") and not url.startswith("//") else "/panel"


@router.post("/confirmar/{codigo}")
def confirmar(request: Request, codigo: str, decision: str = Form(...),
              volver: str = Form("/panel"), csrf: str = Form(""),
              ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    volver = _seguro(volver)
    try:
        if decision == "si":
            res = _accion(ctx, "confirmar_accion", {"codigo": codigo},
                          lambda s: confirmar_accion(s, ctx, codigo))
            return _volver(volver, ok=f"Hecho: {res['accion']}")
        _accion(ctx, "rechazar_accion", {"codigo": codigo},
                lambda s: rechazar_accion(s, ctx, codigo))
        return _volver(volver, ok="Cancelado: no se ha hecho nada.")
    except ErrorNegocio as e:
        if "caducado" in str(e):  # se guarda el estado EXPIRADA
            with sesion() as s:
                from app.db.modelos import AccionPendiente, EstadoAccion

                a = s.get(AccionPendiente, codigo)
                if a is not None and a.estado == EstadoAccion.PENDIENTE:
                    a.estado = EstadoAccion.EXPIRADA
        return _volver(volver, error=str(e))


# ------------------------------------------------------------------ clientes (RF-PAN-005)


@router.get("/clientes", response_class=HTMLResponse)
def ver_clientes(request: Request, q: str = "",
                 ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        encontrados = clientes.buscar_cliente(s, ctx, q.strip())
    return _pagina(request, "clientes.html", ctx, q=q, encontrados=encontrados)


@router.get("/clientes/{cliente_id}", response_class=HTMLResponse)
def ficha(request: Request, cliente_id: int,
          ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        f = clientes.ficha_cliente(s, ctx, cliente_id)
        h = panel.historial_cliente(s, ctx, cliente_id)
        prods = panel.productos(s, ctx) if ctx.rol >= Rol.ADMIN else []
    return _pagina(request, "ficha.html", ctx, f=f, h=h, productos=prods)


@router.post("/clientes/{cliente_id}/nota")
def nueva_nota(request: Request, cliente_id: int, texto: str = Form(""), csrf: str = Form(""),
               ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    if not texto.strip():
        return _volver(f"/panel/clientes/{cliente_id}", error="La nota está vacía.")
    _accion(ctx, "nota_interna", {"cliente_id": cliente_id},
            lambda s: clientes.nota_interna(s, ctx, cliente_id, texto.strip()))
    return _volver(f"/panel/clientes/{cliente_id}", ok="Nota guardada.")


# ------------------------------------------------------------------ caja (RF-PAN-006)


def _periodo(ctx: Contexto, s: Session, periodo: str) -> tuple[date, date]:
    hoy = ctx.ahora.astimezone(zona(s, ctx.estudio_id)).date()
    if periodo == "semana":
        return hoy - timedelta(days=hoy.weekday()), hoy
    if periodo == "mes":
        return hoy.replace(day=1), hoy
    return hoy, hoy


@router.get("/caja", response_class=HTMLResponse)
def caja(request: Request, periodo: str = "dia",
         ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        desde, hasta = _periodo(ctx, s, periodo)
        resumen = pagos.resumen_caja(s, ctx, desde, hasta)
        lista = panel.pagos_periodo(s, ctx, desde, hasta)
        clis = panel.lista_clientes(s, ctx)
        prods = panel.productos(s, ctx)
    return _pagina(request, "caja.html", ctx, periodo=periodo, resumen=resumen, pagos=lista,
                   clientes=clis, productos=prods,
                   cliente_sel=request.query_params.get("cliente"))


@router.post("/caja/pago")
def pago_manual(request: Request, cliente_id: int = Form(...), producto: str = Form(...),
                metodo: str = Form(...), importe: str = Form(""), volver: str = Form("/panel/caja"),
                csrf: str = Form(""), ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    volver = _seguro(volver)
    try:
        cantidad = float(importe.replace(",", ".")) if importe.strip() else None
        res = _accion(ctx, "registrar_pago_manual", {"cliente_id": cliente_id},
                      lambda s: pagos.registrar_pago_manual(s, ctx, cliente_id, producto,
                                                            metodo, cantidad))
    except SinPermiso:
        raise
    except (ErrorNegocio, ValueError) as e:
        return _volver(volver, error=str(e))
    return _tras_accion(res, volver, "Pago registrado.")


@router.post("/caja/devolver/{pago_id}")
def devolver(request: Request, pago_id: int, motivo: str = Form(""),
             csrf: str = Form(""), ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    try:
        res = _accion(ctx, "devolver_pago", {"pago_id": pago_id},
                      lambda s: pagos.devolver_pago(s, ctx, pago_id,
                                                    motivo.strip() or "a petición del estudio"))
    except SinPermiso:
        raise
    except ErrorNegocio as e:
        return _volver("/panel/caja", error=str(e))
    return _tras_accion(res, "/panel/caja", "Devolución hecha.")


# ------------------------------------------------------------------ campañas (RF-PAN-007)


def _filtro(nivel: str, modalidad: str, bono: str, inactivos: str) -> dict[str, Any]:
    return {"nivel": nivel or None, "modalidad": modalidad or None,
            "con_bono_activo": {"si": True, "no": False}.get(bono),
            "inactivos_dias": int(inactivos) if inactivos.isdigit() else None}


@router.get("/campanas", response_class=HTMLResponse)
def campanas(request: Request, nivel: str = "", modalidad: str = "", bono: str = "",
             inactivos: str = "", ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    from sqlalchemy import select

    from app.db.modelos import Campana

    with sesion() as s:
        seg = marketing.segmentar(s, ctx, **_filtro(nivel, modalidad, bono, inactivos))
        anteriores = [{"id": c.id, "texto": c.texto, "estado": c.estado,
                       "total": c.segmento.get("total", 0),
                       "enviada": c.enviada_en.strftime("%d/%m/%Y") if c.enviada_en else ""}
                      for c in s.scalars(select(Campana).where(
                          Campana.estudio_id == ctx.estudio_id).order_by(Campana.id.desc())
                          .limit(10))]
    return _pagina(request, "campanas.html", ctx, seg=seg, anteriores=anteriores,
                   f={"nivel": nivel, "modalidad": modalidad, "bono": bono,
                      "inactivos": inactivos})


@router.post("/campanas")
def crear_campana(request: Request, texto: str = Form(""), nivel: str = Form(""),
                  modalidad: str = Form(""), bono: str = Form(""), inactivos: str = Form(""),
                  csrf: str = Form(""), ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    if not texto.strip():
        return _volver("/panel/campanas", error="Escribe el texto de la campaña.")
    filtro = _filtro(nivel, modalidad, bono, inactivos)
    try:
        def crear_y_pedir(s: Session) -> dict[str, Any]:
            b = marketing.borrador_campana(s, ctx, texto.strip(), **filtro)
            return marketing.enviar_campana(s, ctx, b["campana_id"])

        res = _accion(ctx, "campana", filtro, crear_y_pedir)
    except SinPermiso:
        raise
    except ErrorNegocio as e:
        return _volver("/panel/campanas", error=str(e))
    return _tras_accion(res, "/panel/campanas", "Campaña enviada.")


# ------------------------------------------------------------------ conversaciones (RF-PAN-008)


@router.get("/conversaciones", response_class=HTMLResponse)
def conversaciones(request: Request, ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        datos = panel.conversaciones_panel(s, ctx)
    return _pagina(request, "conversaciones.html", ctx, **datos)


@router.get("/conversaciones/{conversacion_id}", response_class=HTMLResponse)
def conversacion(request: Request, conversacion_id: int,
                 ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    with sesion() as s:
        conv = panel.ver_conversacion(s, ctx, conversacion_id)
    return _pagina(request, "conversacion.html", ctx, conv=conv)


@router.post("/conversaciones/{conversacion_id}/responder")
def responder(request: Request, conversacion_id: int, texto: str = Form(""),
              csrf: str = Form(""), ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    volver = f"/panel/conversaciones/{conversacion_id}"
    try:
        _accion(ctx, "responder", {"conversacion_id": conversacion_id},
                lambda s: panel.responder_conversacion(s, ctx, conversacion_id, texto))
    except ErrorNegocio as e:
        return _volver(volver, error=str(e))
    return _volver(volver, ok="Mensaje enviado.")


@router.post("/conversaciones/{conversacion_id}/reactivar")
def reactivar(request: Request, conversacion_id: int, csrf: str = Form(""),
              ctx: Contexto = Depends(staff_actual)) -> RedirectResponse:
    _comprobar_csrf(request, csrf)
    _accion(ctx, "reactivar", {"conversacion_id": conversacion_id},
            lambda s: panel.reactivar_conversacion(s, ctx, conversacion_id))
    return _volver(f"/panel/conversaciones/{conversacion_id}",
                   ok="El asistente vuelve a atender esta conversación.")


# ------------------------------------------------------------------ informes (RF-PAN-009)


@router.get("/informes", response_class=HTMLResponse)
def informes(request: Request, ctx: Contexto = Depends(staff_actual)) -> HTMLResponse:
    from app.servicios.base import politicas

    with sesion() as s:
        informe = marketing.informe_semanal(s, ctx)
        dias = int(politicas(s, ctx.estudio_id)["inactividad_dias"])
        inactivos = marketing.clientes_inactivos(s, ctx, dias)
    return _pagina(request, "informes.html", ctx, informe=informe, inactivos=inactivos,
                   dias=dias)


# ------------------------------------------------------------------ simulador (RF-PAN-012)


def _canal_web() -> CanalWeb:
    if get_settings().canal != "consola":
        raise HTTPException(404)
    canal = get_canal()
    if not isinstance(canal, CanalWeb):
        raise HTTPException(404)
    return canal


@simulador.get("/chat", response_class=HTMLResponse)
def chat(request: Request, tel: str = "", otro: str = "") -> HTMLResponse:
    canal = _canal_web()
    tel = otro or tel
    with sesion() as s:
        from sqlalchemy import select

        from app.db.modelos import Cliente, EstadoCliente

        staff = [(st.telefono, f"{st.nombre} · {st.rol}") for st in s.scalars(
            select(Staff).where(Staff.estudio_id == _estudio_id(), Staff.activo.is_(True))
            .order_by(Staff.id))]
        clis = [(c.telefono, f"{c.nombre} · cliente") for c in s.scalars(
            select(Cliente).where(Cliente.estudio_id == _estudio_id(),
                                  Cliente.estado == EstadoCliente.ACTIVO)
            .order_by(Cliente.id).limit(8))]
    tel = acceso.normalizar_telefono(tel) if tel else ""
    burbujas = canal.chats.get(tel, []) if tel else []
    return plantillas.TemplateResponse(request, "chat.html", {
        "tel": tel, "burbujas": burbujas, "staff": staff, "clientes": clis})


@simulador.post("/chat")
def chat_enviar(tel: str = Form(...), texto: str = Form(""),
                respuesta_id: str = Form("")) -> RedirectResponse:
    canal = _canal_web()
    tel = acceso.normalizar_telefono(tel)
    if texto.strip() or respuesta_id:
        canal.entrante(tel, texto.strip())
        procesar_mensaje(_estudio_id(), tel, texto.strip(), canal=canal,
                         respuesta_id=respuesta_id or None)
    return RedirectResponse(f"/dev/chat?tel={quote(tel)}#fin", status_code=303)
