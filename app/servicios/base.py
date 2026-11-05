"""Piezas comunes de los servicios: contexto del turno, roles, errores y avisos.

La lógica de negocio vive aquí y no en los prompts (constitución §4.3).
Los permisos se comprueban en el código (RNF-SEG-002).
"""

from __future__ import annotations

import enum
import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Concatenate, ParamSpec, TypeVar

from sqlalchemy.orm import Session

from app.db.modelos import Estudio


class Rol(enum.IntEnum):
    """Roles ordenados de menos a más permisos."""

    LEAD = 0
    CLIENTE = 1
    INSTRUCTOR = 2
    ADMIN = 3


class ErrorNegocio(Exception):
    """Error que se le puede explicar al usuario tal cual."""


class SinPermiso(ErrorNegocio):
    pass


class NoEncontrado(ErrorNegocio):
    pass


@dataclass
class Aviso:
    """Mensaje saliente a alguien que no es el remitente (o fuera de la respuesta).

    Si la ventana de 24 h está cerrada, se usa `plantilla` con `parametros` (diseño §8).
    """

    telefono: str
    texto: str
    plantilla: str | None = None
    parametros: list[str] = field(default_factory=list)


@dataclass
class Contexto:
    """Quién escribe y en qué estudio. Lo construye el código, nunca el modelo."""

    estudio_id: int
    telefono: str
    rol: Rol
    nombre: str | None = None
    cliente_id: int | None = None
    staff_id: int | None = None
    ahora: datetime = field(default_factory=lambda: datetime.now(UTC))
    conversacion_id: int | None = None
    avisos: list[Aviso] = field(default_factory=list)
    acciones_creadas: list[dict[str, Any]] = field(default_factory=list)

    def exigir_cliente(self) -> int:
        if self.cliente_id is None:
            raise ErrorNegocio(
                "Todavía no tienes ficha en el estudio. Primero hay que darte de alta."
            )
        return self.cliente_id

    @classmethod
    def sistema(cls, estudio_id: int, ahora: datetime | None = None) -> Contexto:
        """Contexto para los jobs del cron (actúa como admin)."""
        return cls(estudio_id=estudio_id, telefono="sistema", rol=Rol.ADMIN,
                   ahora=ahora or datetime.now(UTC))


P = ParamSpec("P")
R = TypeVar("R")


def requiere_rol(
    minimo: Rol,
) -> Callable[[Callable[Concatenate[Session, Contexto, P], R]],
              Callable[Concatenate[Session, Contexto, P], R]]:
    """Comprueba el rol del remitente antes de ejecutar el servicio (RNF-SEG-002)."""

    def deco(
        fn: Callable[Concatenate[Session, Contexto, P], R],
    ) -> Callable[Concatenate[Session, Contexto, P], R]:
        @functools.wraps(fn)
        def envoltura(s: Session, ctx: Contexto, /, *args: P.args, **kwargs: P.kwargs) -> R:
            if ctx.rol < minimo:
                raise SinPermiso("No tienes permiso para hacer eso.")
            return fn(s, ctx, *args, **kwargs)

        envoltura.rol_minimo = minimo  # type: ignore[attr-defined]
        return envoltura

    return deco


POLITICAS_POR_DEFECTO: dict[str, Any] = {
    "cancelacion_horas_limite": 12,
    "cancelacion_tardia": "pierde_sesion",
    "lista_espera_horas_para_aceptar": 2,
    "max_reservas_futuras": 8,
    "congelacion_permitida": True,
    "congelacion_max_dias": 30,
    "inactividad_dias": 21,
    "recordatorios_horas_antes": [24, 2],
    "impagos_dias": [0, 3, 7],
    "resena_tras_clases": 3,
    "humano_horas": 4,
}


def politicas(s: Session, estudio_id: int) -> dict[str, Any]:
    estudio = s.get(Estudio, estudio_id)
    if estudio is None:
        raise NoEncontrado(f"Estudio {estudio_id} no existe")
    return {**POLITICAS_POR_DEFECTO, **estudio.config.get("politicas", {})}


def datos_estudio(s: Session, estudio_id: int) -> dict[str, Any]:
    estudio = s.get(Estudio, estudio_id)
    if estudio is None:
        raise NoEncontrado(f"Estudio {estudio_id} no existe")
    datos: dict[str, Any] = estudio.config.get("estudio", {})
    return {"nombre": estudio.nombre, "zona_horaria": estudio.zona_horaria, **datos}


def a_utc(dt: datetime) -> datetime:
    """SQLite devuelve datetimes sin zona; los normalizamos a UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def mostrar_telefono(tel: str) -> str:
    """RF-DEMO-006: en la demo los teléfonos se enmascaran."""
    from config.settings import get_settings

    if not get_settings().demo or len(tel) < 7:
        return tel
    return f"{tel[:4]}•• ••• {tel[-3:]}"
