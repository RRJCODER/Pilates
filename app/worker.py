"""Worker: procesa mensajes de WhatsApp y ejecuta el cron (arq).

    arq app.worker.WorkerSettings

Los mensajes de un mismo número se procesan de uno en uno con un lock en Redis
para no cruzar respuestas (diseño §8).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from arq import cron
from arq.connections import RedisSettings

from app.conversacion import entregar, procesar_mensaje
from app.db.seed import extender_horario
from app.db.sesion import sesion
from app.integraciones.canal import CanalWhatsApp, get_canal
from app.integraciones.stt import get_stt
from app.servicios import bonos, conversaciones, lista_espera, marketing, pagos
from app.servicios.base import Aviso, Contexto, politicas
from app.servicios.clientes import telefonos_admin
from config.settings import get_settings

log = logging.getLogger(__name__)

NO_AUDIO = ("[El cliente ha enviado una nota de voz que no se ha podido transcribir. Pídele "
            "con amabilidad que lo escriba.]")


def texto_de_mensaje(m: dict[str, Any]) -> str:
    """Convierte audio e imágenes en texto para el orquestador (RF-ORQ-006)."""
    tipo, texto = m.get("tipo"), m.get("texto") or ""
    canal = get_canal()
    if tipo in ("audio", "voice"):
        if isinstance(canal, CanalWhatsApp):
            audio, mime = canal.descargar_medio(m["media_id"])
            transcrito = get_stt().transcribir(audio, mime)
            return f"[Nota de voz transcrita] {transcrito}" if transcrito else NO_AUDIO
        return NO_AUDIO
    if tipo in ("image", "document"):
        estudio_id = get_settings().estudio_id
        with sesion() as s:
            admins = telefonos_admin(s, estudio_id)
        entregar(estudio_id, [Aviso(t, f"📎 {m['telefono']} ha enviado un {tipo}"
                                       f"{' («' + texto + '»)' if texto else ''}. "
                                       "Revísalo en WhatsApp Business.") for t in admins])
        return (f"[El cliente ha enviado un archivo ({tipo}) que se ha reenviado al equipo"
                f"{'; pie: ' + texto if texto else ''}. Si parece un justificante de pago, "
                "dile que el equipo lo comprobará; no des nada por pagado.]")
    return texto


def procesar_whatsapp_sync(m: dict[str, Any]) -> None:
    procesar_mensaje(get_settings().estudio_id, m["telefono"], texto_de_mensaje(m),
                     wa_message_id=m["wa_message_id"], respuesta_id=m.get("respuesta_id"))


async def procesar_whatsapp(ctx: dict[str, Any], m: dict[str, Any]) -> None:
    redis = ctx["redis"]
    async with redis.lock(f"lock:tel:{m['telefono']}", timeout=180, blocking_timeout=170):
        await asyncio.to_thread(procesar_whatsapp_sync, m)


# ------------------------------------------------------------------ cron


def _tarea(nombre: str, fn: Callable[[Any, Contexto], Any]) -> Callable[..., Any]:
    async def ejecutar(_: dict[str, Any]) -> None:
        estudio_id = get_settings().estudio_id

        def sync() -> None:
            ctx = Contexto.sistema(estudio_id, datetime.now(UTC))
            with sesion() as s:
                res = fn(s, ctx)
            log.info("cron %s: %s", nombre, res)
            entregar(estudio_id, ctx.avisos)

        await asyncio.to_thread(sync)

    ejecutar.__name__ = ejecutar.__qualname__ = nombre
    return ejecutar


def _hoy(s: Any, ctx: Contexto) -> Any:
    from app.servicios.clases import zona

    return ctx.ahora.astimezone(zona(s, ctx.estudio_id)).date()


cada_15 = _tarea("recordatorios_y_espera", lambda s, ctx: {
    "recordatorios": marketing.recordatorios_clase(
        s, ctx, politicas(s, ctx.estudio_id)["recordatorios_horas_antes"]),
    "ofertas_caducadas": lista_espera.expirar_ofertas(s, ctx)})

diario_manana = _tarea("diario", lambda s, ctx: {
    "clases_nuevas": extender_horario(s, ctx.estudio_id, _hoy(s, ctx)),
    "bonos": bonos.mantenimiento_bonos(s, ctx.estudio_id, _hoy(s, ctx)),
    "renovaciones": pagos.generar_renovaciones(s, ctx, _hoy(s, ctx)),
    "impagos": pagos.recordar_impagos(s, ctx, _hoy(s, ctx),
                                      politicas(s, ctx.estudio_id)["impagos_dias"]),
    "cumpleanos": marketing.felicitar_cumpleanos(s, ctx, _hoy(s, ctx)),
    "inactivos": marketing.proponer_recuperaciones(
        s, ctx, politicas(s, ctx.estudio_id)["inactividad_dias"], _hoy(s, ctx))})

diario_tarde = _tarea("resenas", lambda s, ctx: marketing.pedir_resenas(
    s, ctx, politicas(s, ctx.estudio_id)["resena_tras_clases"]))

semanal = _tarea("informe_semanal", lambda s, ctx: marketing.enviar_informe_semanal(
    s, ctx, _hoy(s, ctx))["semana"])


async def resumir(_: dict[str, Any]) -> None:
    from app.agentes.resumen import resumir_conversacion

    with sesion() as s:
        ids = conversaciones.conversaciones_a_resumir(s, get_settings().estudio_id)
    for cid in ids:
        try:
            await asyncio.to_thread(resumir_conversacion, cid)
        except Exception:
            log.exception("No se pudo resumir la conversación %s", cid)


async def reinicio_demo(_: dict[str, Any]) -> None:
    """RF-DEMO-004: solo con DEMO=true."""
    if not get_settings().demo:
        return
    from app.db.seed import reiniciar
    from app.db.sesion import get_engine

    await asyncio.to_thread(reiniciar, get_engine(), get_settings().estudio_config)
    log.info("Demo reiniciada")


class WorkerSettings:
    functions = [procesar_whatsapp]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 300
    max_tries = 3
    # Las horas del cron se interpretan en la zona horaria del estudio
    timezone = ZoneInfo("Europe/Madrid")
    cron_jobs = [
        cron(cast(Any, cada_15), minute={0, 15, 30, 45}, run_at_startup=False),
        cron(cast(Any, diario_manana), hour={9}, minute={5}),
        cron(cast(Any, diario_tarde), hour={18}, minute={35}),
        cron(cast(Any, semanal), weekday="mon", hour={8}, minute={10}),
        cron(cast(Any, resumir), minute={20, 50}),
        cron(cast(Any, reinicio_demo), hour={4}, minute={0}),
    ]
