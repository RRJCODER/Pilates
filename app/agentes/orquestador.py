"""Orquestador: entiende el mensaje y delega en los subagentes (diseño §4)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from anthropic import beta_tool
from anthropic.lib.tools import ToolError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agentes.claude import Uso, config_modelo, ejecutar_agente, system_prompt
from app.agentes.herramientas import llamar
from app.agentes.memoria import MemoriaDB, leer_memoria
from app.agentes.subagentes import herramientas_para
from app.db.modelos import AccionPendiente, AmbitoMemoria, EstadoAccion
from app.db.sesion import sesion
from app.servicios import acciones, conversaciones
from app.servicios.base import Contexto, Rol, datos_estudio
from app.servicios.bonos import estado_bono

PROMPTS = Path(__file__).parent / "prompts"
NOMBRE_ROL = {Rol.LEAD: "contacto nuevo (todavía no es cliente)", Rol.CLIENTE: "cliente",
              Rol.INSTRUCTOR: "instructor/a del estudio", Rol.ADMIN: "admin (dueño/a) del estudio"}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


@cache
def _prompt(nombre: str) -> str:
    return (PROMPTS / f"{nombre}.md").read_text(encoding="utf-8")


def _datos_estudio_txt(datos: dict[str, Any]) -> str:
    claves = [("nombre", "Nombre"), ("direccion", "Dirección"),
              ("telefono_contacto", "Teléfono"), ("web", "Web")]
    return "\n".join(f"- {etq}: {datos[k]}" for k, etq in claves if datos.get(k))


def prompt_estable(s: Session, ctx: Contexto) -> tuple[str, str]:
    """Parte cacheable del system prompt: igual para todos los mensajes del estudio."""
    datos = datos_estudio(s, ctx.estudio_id)
    memoria_estudio = leer_memoria(s, ctx, AmbitoMemoria.ESTUDIO)
    orq = _prompt("orquestador").format(
        nombre_estudio=datos["nombre"], tono=datos.get("tono", "cercano"),
        datos_estudio=_datos_estudio_txt(datos),
        memoria_estudio=("## Memoria del estudio (/memories/estudio/)\n" + memoria_estudio)
        if memoria_estudio else "")
    sub = _prompt("subagente_comun").format(nombre_estudio=datos["nombre"])
    return orq, sub


def prompt_variable(s: Session, ctx: Contexto, resumen: str | None) -> str:
    """Parte que cambia en cada turno (después del punto de caché)."""
    tz = ZoneInfo(datos_estudio(s, ctx.estudio_id)["zona_horaria"])
    ahora = ctx.ahora.astimezone(tz)
    partes = [
        f"# Ahora\nHoy es {DIAS[ahora.weekday()]} {ahora:%Y-%m-%d}, son las {ahora:%H:%M} "
        f"(hora del estudio). Mañana es {(ahora + timedelta(days=1)):%Y-%m-%d}.",
        f"# Quién escribe\nRol: {NOMBRE_ROL[ctx.rol]}. Nombre: {ctx.nombre or 'desconocido'}.",
    ]
    if ctx.rol == Rol.CLIENTE:
        try:
            eb = estado_bono(s, ctx)
            partes.append(f"Sesiones disponibles: {eb['sesiones_disponibles']}. "
                          f"Bonos: {json.dumps(eb['bonos'], ensure_ascii=False)}")
        except Exception:  # noqa: BLE001 - el resumen de ficha es opcional
            pass
    if ctx.rol == Rol.LEAD:
        partes.append("Es un contacto nuevo: preséntale el estudio, ofrécele la clase de "
                      "prueba y, si quiere, dalo de alta (vía delegar_clientes).")
    memoria = leer_memoria(s, ctx, AmbitoMemoria.CLIENTE)
    if memoria:
        partes.append("# Lo que recuerdas de esta persona\n" + memoria)
    if resumen:
        partes.append("# Resumen de la conversación anterior\n" + resumen)
    pendientes = list(s.scalars(select(AccionPendiente).where(
        AccionPendiente.estudio_id == ctx.estudio_id,
        AccionPendiente.quien_confirma == ctx.telefono,
        AccionPendiente.estado == EstadoAccion.PENDIENTE,
        AccionPendiente.expira_en > ctx.ahora)))
    if pendientes:
        partes.append("# Acciones pendientes de confirmar por esta persona\n" + "\n".join(
            f"- código {a.codigo}: {a.descripcion}" for a in pendientes))
    return "\n\n".join(partes)


def herramientas_orquestador(ctx: Contexto, prompt_sub: str, variable: str,
                             uso: Uso) -> list[Any]:
    modelo, effort = config_modelo("subagente")

    def delegar(subagente: str, tarea: str) -> str:
        antes = len(ctx.acciones_creadas)
        sistema = system_prompt(prompt_sub + "\n\n" + _prompt(subagente), variable)
        resumen = ejecutar_agente(
            agente=subagente, ctx=ctx, modelo=modelo, effort=effort, system=sistema,
            tools=herramientas_para(subagente, ctx),
            mensajes=[{"role": "user", "content": tarea}], uso=uso)
        return json.dumps({"resumen": resumen,
                           "acciones_pendientes": ctx.acciones_creadas[antes:]},
                          ensure_ascii=False)

    @beta_tool
    def delegar_reservas(tarea: str) -> str:
        """Delega en el especialista de reservas y clases.

        Args:
            tarea: Instrucción completa y autocontenida, con fechas AAAA-MM-DD.
        """
        return delegar("reservas", tarea)

    @beta_tool
    def delegar_clientes(tarea: str) -> str:
        """Delega en el especialista de clientes, altas, bonos, lesiones y consentimientos.

        Args:
            tarea: Instrucción completa y autocontenida.
        """
        return delegar("clientes", tarea)

    @beta_tool
    def delegar_cobros(tarea: str) -> str:
        """Delega en el especialista de pagos, enlaces de pago y facturas.

        Args:
            tarea: Instrucción completa y autocontenida.
        """
        return delegar("cobros", tarea)

    @beta_tool
    def delegar_marketing(tarea: str) -> str:
        """Delega en el especialista de marketing: BAJA de promociones; para el admin,
        campañas, inactivos e informe semanal.

        Args:
            tarea: Instrucción completa y autocontenida.
        """
        return delegar("marketing", tarea)

    @beta_tool
    def derivar_a_humano(motivo: str, urgente: bool = False) -> str:
        """Avisa al equipo del estudio para que una persona atienda esta conversación.

        Args:
            motivo: Resumen breve de por qué hace falta una persona.
            urgente: true si es urgente.
        """
        return llamar(ctx, "orquestador", "derivar_a_humano",
                      conversaciones.derivar_a_humano, motivo=motivo, urgente=urgente)

    @beta_tool
    def confirmar_accion(codigo: str) -> str:
        """Ejecuta una acción pendiente después de que la persona haya respondido SÍ.

        Args:
            codigo: Código de 4 caracteres de la acción pendiente.
        """
        return llamar(ctx, "orquestador", "confirmar_accion", acciones.confirmar_accion,
                      codigo=codigo)

    @beta_tool
    def rechazar_accion(codigo: str) -> str:
        """Descarta una acción pendiente si la persona dice que no.

        Args:
            codigo: Código de la acción pendiente.
        """
        return llamar(ctx, "orquestador", "rechazar_accion", acciones.rechazar_accion,
                      codigo=codigo)

    tools: list[Any] = [delegar_reservas, delegar_clientes, delegar_cobros, delegar_marketing,
                        derivar_a_humano, confirmar_accion, rechazar_accion,
                        MemoriaDB(ctx, sesion)]
    if ctx.rol == Rol.ADMIN:
        @beta_tool
        def reactivar_asistente(telefono: str) -> str:
            """(Admin) Devuelve el control al asistente en una conversación derivada.

            Args:
                telefono: Teléfono de la conversación, con prefijo (+34…).
            """
            return llamar(ctx, "orquestador", "reactivar_asistente",
                          conversaciones.reactivar_bot, telefono=telefono)

        tools.append(reactivar_asistente)
    return tools


def responder(ctx: Contexto, mensajes: list[dict[str, Any]], resumen: str | None,
              uso: Uso | None = None) -> str:
    """Ejecuta el orquestador sobre el historial y devuelve el texto para WhatsApp."""
    uso = uso or Uso()
    with sesion() as s:
        estable, prompt_sub = prompt_estable(s, ctx)
        variable = prompt_variable(s, ctx, resumen)
    modelo, effort = config_modelo("orquestador")
    texto = ejecutar_agente(
        agente="orquestador", ctx=ctx, modelo=modelo, effort=effort,
        system=system_prompt(estable, variable),
        tools=herramientas_orquestador(ctx, prompt_sub, variable, uso),
        mensajes=mensajes, uso=uso)
    if not texto:
        raise ToolError("El orquestador no produjo respuesta")
    return texto


Responder = Callable[[Contexto, list[dict[str, Any]], str | None], str]
