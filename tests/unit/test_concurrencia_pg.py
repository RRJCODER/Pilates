"""E-RES-05: dos personas reservan la última plaza a la vez (solo en PostgreSQL).

Se ejecuta si existe TEST_DATABASE_URL, por ejemplo:
    TEST_DATABASE_URL=postgresql+psycopg://pilates:pilates@localhost:5432/pilates_test
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text

from app.db.modelos import Base, Bono, Clase, Cliente, EstadoBono, Producto, Reserva
from app.db.seed import seed
from app.db.sesion import sesion
from app.servicios.base import Contexto, ErrorNegocio
from app.servicios.clientes import identificar
from app.servicios.reservas import reservar_clase
from tests.conftest import CFG, LUNES

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Necesita TEST_DATABASE_URL (PostgreSQL)")


def test_ultima_plaza_concurrente() -> None:
    assert URL
    engine = create_engine(URL)
    with engine.begin() as con:
        con.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    seed(engine, CFG, desde=LUNES)
    ahora = datetime(2026, 11, 1, 10, tzinfo=UTC)

    with sesion(engine) as s:
        clase = s.scalars(select(Clase).order_by(Clase.inicio)).first()
        assert clase is not None
        clase.aforo = 1
        clase_id = clase.id
        bono10 = s.scalar(select(Producto).where(Producto.clave == "bono10"))
        assert bono10 is not None
        telefonos = []
        for i in range(2):
            c = Cliente(estudio_id=1, nombre=f"C{i}", telefono=f"+3477700000{i}")
            s.add(c)
            s.flush()
            s.add(Bono(estudio_id=1, cliente_id=c.id, producto_id=bono10.id, sesiones_total=10,
                       inicio=ahora.date(), caduca=ahora.date() + timedelta(days=60),
                       estado=EstadoBono.ACTIVO))
            telefonos.append(c.telefono)

    barrera = threading.Barrier(2)
    resultados: list[str] = []

    def intentar(tel: str) -> None:
        try:
            with sesion(engine) as s:
                ctx: Contexto = identificar(s, 1, tel)
                ctx.ahora = ahora
                barrera.wait()
                resultados.append(reservar_clase(s, ctx, clase_id)["resultado"])
        except ErrorNegocio as e:
            resultados.append(f"error: {e}")

    hilos = [threading.Thread(target=intentar, args=(t,)) for t in telefonos]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert sorted(resultados) == ["clase_llena", "ok"]
    with sesion(engine) as s:
        assert len(s.scalars(select(Reserva).where(Reserva.clase_id == clase_id)).all()) == 1
