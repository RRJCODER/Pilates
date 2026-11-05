"""Acciones que requieren confirmación (diseño §7, constitución §2.2).

Una herramienta marcada con ✋ no ejecuta nada: llama a `crear_accion` y la acción
queda pendiente. Solo `confirmar_accion` la ejecuta, y únicamente si quien confirma
es la persona prevista y la acción no ha caducado.
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.modelos import AccionPendiente, EstadoAccion
from app.servicios.base import Contexto, ErrorNegocio, NoEncontrado, a_utc

MINUTOS_VALIDEZ = 15

Ejecutor = Callable[[Session, Contexto, dict[str, Any]], dict[str, Any]]
_EJECUTORES: dict[str, Ejecutor] = {}


def ejecutor(tipo: str) -> Callable[[Ejecutor], Ejecutor]:
    """Registra la función que ejecuta un tipo de acción una vez confirmada."""

    def deco(fn: Ejecutor) -> Ejecutor:
        _EJECUTORES[tipo] = fn
        return fn

    return deco


def _codigo() -> str:
    alfabeto = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(4))


def crear_accion(s: Session, ctx: Contexto, tipo: str, descripcion: str,
                 payload: dict[str, Any], quien_confirma: str | None = None) -> dict[str, Any]:
    if tipo not in _EJECUTORES:
        raise ValueError(f"Tipo de acción sin ejecutor: {tipo}")
    codigo = _codigo()
    while s.get(AccionPendiente, codigo) is not None:
        codigo = _codigo()
    accion = AccionPendiente(
        codigo=codigo, estudio_id=ctx.estudio_id, tipo=tipo, descripcion=descripcion,
        payload=payload, quien_confirma=quien_confirma or ctx.telefono,
        expira_en=ctx.ahora + timedelta(minutes=MINUTOS_VALIDEZ),
    )
    s.add(accion)
    s.flush()
    info = {"resultado": "requiere_confirmacion", "codigo": codigo, "descripcion": descripcion,
            "expira_en_minutos": MINUTOS_VALIDEZ}
    ctx.acciones_creadas.append(info)
    return info


def confirmar_accion(s: Session, ctx: Contexto, codigo: str) -> dict[str, Any]:
    accion = s.get(AccionPendiente, codigo.strip().upper(), with_for_update=True)
    if accion is None or accion.estudio_id != ctx.estudio_id:
        raise NoEncontrado("No encuentro ninguna acción pendiente con ese código.")
    if accion.quien_confirma != ctx.telefono:
        raise ErrorNegocio("Esa acción la tiene que confirmar otra persona.")
    if accion.estado != EstadoAccion.PENDIENTE:
        raise ErrorNegocio(f"Esa acción ya está {accion.estado}.")
    if a_utc(accion.expira_en) < ctx.ahora:
        accion.estado = EstadoAccion.EXPIRADA
        raise ErrorNegocio("La confirmación ha caducado. Vuelve a pedirlo si sigue haciendo falta.")
    resultado = _EJECUTORES[accion.tipo](s, ctx, accion.payload)
    accion.estado = EstadoAccion.EJECUTADA
    return {"resultado": "ok", "accion": accion.descripcion, **resultado}


def rechazar_accion(s: Session, ctx: Contexto, codigo: str) -> dict[str, Any]:
    accion = s.get(AccionPendiente, codigo.strip().upper())
    if accion is None or accion.quien_confirma != ctx.telefono:
        raise NoEncontrado("No encuentro esa acción.")
    accion.estado = EstadoAccion.RECHAZADA
    return {"resultado": "ok", "accion": accion.descripcion, "estado": "descartada"}
