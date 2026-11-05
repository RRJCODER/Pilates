"""Conversión de servicios de negocio en herramientas para Claude.

Cada llamada a herramienta abre su propia transacción, comprueba el rol (lo hace
el servicio con @requiere_rol), registra el evento y devuelve JSON. Los errores de
negocio vuelven al modelo como `is_error` con un texto que puede explicar al usuario.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from anthropic.lib.tools import ToolError
from sqlalchemy.orm import Session

from app.agentes.claude import registrar_evento
from app.db.modelos import Estudio
from app.db.sesion import sesion
from app.servicios.base import Contexto, ErrorNegocio


def llamar(ctx: Contexto, agente: str, nombre: str,
           servicio: Callable[..., Any], /, **kwargs: Any) -> str:
    inicio = time.monotonic()
    try:
        with sesion() as s:
            resultado = servicio(s, ctx, **kwargs)
    except ErrorNegocio as e:
        registrar_evento(ctx, agente, nombre, kwargs, {"error": str(e)},
                         latencia_ms=int((time.monotonic() - inicio) * 1000))
        raise ToolError(str(e)) from e
    registrar_evento(ctx, agente, nombre, kwargs, resultado,
                     latencia_ms=int((time.monotonic() - inicio) * 1000))
    return json.dumps(resultado, ensure_ascii=False, default=str)


def zona_estudio(ctx: Contexto) -> ZoneInfo:
    with sesion() as s:
        e = s.get(Estudio, ctx.estudio_id)
        return ZoneInfo(e.zona_horaria if e else "Europe/Madrid")


def fecha_hora(texto: str, tz: ZoneInfo) -> datetime:
    """'2026-11-05' o '2026-11-05T19:00' (hora local del estudio) → datetime UTC."""
    try:
        dt = datetime.fromisoformat(texto.strip())
    except ValueError as e:
        raise ToolError(f"Fecha no válida: {texto!r}. Usa AAAA-MM-DD o AAAA-MM-DDTHH:MM") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


def rango_dias(desde: str, hasta: str | None, tz: ZoneInfo) -> tuple[datetime, datetime]:
    ini = fecha_hora(desde, tz)
    fin = fecha_hora(hasta, tz) if hasta else ini
    if len(hasta or desde) <= 10:  # solo fecha → incluir el día completo
        fin = fin + timedelta(days=1)
    return ini, fin


def fecha(texto: str) -> date:
    try:
        return date.fromisoformat(texto.strip())
    except ValueError as e:
        raise ToolError(f"Fecha no válida: {texto!r}. Usa AAAA-MM-DD") from e


Servicio = Callable[[Session, Contexto], Any]
