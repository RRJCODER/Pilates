"""Fixtures comunes: estudio de ejemplo cargado en SQLite en memoria."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.servicios  # noqa: F401  (registra ejecutores)
from app.db.modelos import Bono, Clase, Cliente, EstadoBono, Producto, Staff, TipoClase
from app.db.seed import seed
from app.db.sesion import sesion
from app.integraciones.pagos import PagosFake, set_pagos
from app.servicios.base import Contexto, Rol
from app.servicios.clientes import identificar

CFG = Path(__file__).parents[1] / "config" / "estudio.toml"
LUNES = date(2026, 11, 2)
# Domingo 1/11/2026 a las 10:00 UTC: justo antes de la primera semana de clases
AHORA = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
ADMIN = "+34600000001"
LAURA = "+34600000002"
MARTA = "+34600000004"


@pytest.fixture
def engine() -> Engine:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    seed(eng, CFG, desde=LUNES)
    return eng


@pytest.fixture(autouse=True)
def pagos_fake() -> Iterator[PagosFake]:
    fake = PagosFake("http://test")
    set_pagos(fake)
    yield fake
    set_pagos(None)


@pytest.fixture
def s(engine: Engine) -> Iterator[Session]:
    with sesion(engine) as ses:
        yield ses


def ctx_de(s: Session, telefono: str, ahora: datetime = AHORA) -> Contexto:
    ctx = identificar(s, 1, telefono)
    ctx.ahora = ahora
    return ctx


@pytest.fixture
def nuevo_cliente(s: Session) -> Callable[..., Contexto]:
    """Crea un cliente con un bono activo y devuelve su contexto."""
    contador = iter(range(100, 999))

    def crear(nombre: str = "Lucía Prueba", bono: str | None = "bono10", usadas: int = 0,
              ahora: datetime = AHORA, **campos: Any) -> Contexto:
        tel = f"+3469{next(contador):07d}"
        cli = Cliente(estudio_id=1, nombre=nombre, telefono=tel, nivel="inicial", **campos)
        s.add(cli)
        s.flush()
        if bono:
            p = s.scalar(select(Producto).where(Producto.clave == bono))
            assert p is not None
            s.add(Bono(estudio_id=1, cliente_id=cli.id, producto_id=p.id,
                       sesiones_total=p.sesiones, sesiones_usadas=usadas,
                       inicio=ahora.date(), caduca=ahora.date() + timedelta(days=p.validez_dias),
                       estado=EstadoBono.ACTIVO))
            s.flush()
        return ctx_de(s, tel, ahora)

    return crear


def clase(s: Session, dia: date, hora_utc: int, clave_tipo: str) -> Clase:
    c = s.scalar(select(Clase).join(TipoClase).where(
        TipoClase.clave == clave_tipo,
        Clase.inicio == datetime(dia.year, dia.month, dia.day, hora_utc, tzinfo=UTC)))
    assert c is not None, (dia, hora_utc, clave_tipo)
    return c


def staff_ctx(s: Session, telefono: str) -> Contexto:
    st = s.scalar(select(Staff).where(Staff.telefono == telefono))
    assert st is not None
    c = ctx_de(s, telefono)
    assert c.rol in (Rol.ADMIN, Rol.INSTRUCTOR)
    return c
