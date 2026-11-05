"""Modo demo (RF-DEMO-*; escenarios E-DEMO-01..04)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.pool import StaticPool

from app.db.modelos import Bono, Campana, Cliente, Reserva
from app.db.seed import reiniciar, seed
from app.db.sesion import sesion
from config.settings import get_settings
from tests.conftest import ADMIN, CFG
from tests.unit.test_menu import Chat, poner_plan


@pytest.fixture
def demo(entorno: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    monkeypatch.setattr(get_settings(), "demo", True)
    poner_plan(entorno, "basico")
    yield entorno


def _alta(tel: str, nombre: str) -> Chat:
    chat = Chat(tel)
    chat.escribir("hola")
    chat.pulsar("Darme de alta")
    chat.escribir(nombre)
    chat.pulsar("Acepto")
    chat.pulsar("Sí, quiero")
    return chat


def test_e_demo_01_cliente_con_bono_de_regalo(demo: Engine) -> None:
    chat = _alta("+34699000001", "Ana Visitante")
    assert "Esto es una *demo*" in chat.canal.enviados[0][1]
    chat.escribir("menu")
    chat.pulsar("Reservar clase")
    chat.pulsar("Mañana")
    chat.pulsar("19:00 Reformer")
    assert "Te quedan 4 sesiones" in chat.ultimo


def test_e_demo_02_cambiar_de_rol(demo: Engine) -> None:
    chat = _alta("+34699000002", "Bea Visitante")
    chat.escribir("DEMO ADMIN")
    assert "Caja de hoy" in chat.ultimo
    chat.escribir("demo cliente")
    assert "Mi bono" in chat.ultimo
    chat.pulsar("Mi bono")
    assert "te quedan *5*" in chat.ultimo


def test_demo_desactivada_no_da_permisos(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    chat = _alta("+34699000003", "Carla")
    chat.escribir("DEMO ADMIN")
    assert "Caja de hoy" not in chat.ultimo
    with sesion(entorno) as s:  # y sin bono de regalo
        assert not s.scalars(select(Bono)).all()


def test_e_demo_04_privacidad_entre_visitantes(demo: Engine) -> None:
    a = _alta("+34699000004", "Dani Visitante")
    b = _alta("+34699000005", "Eva Visitante")
    b.escribir("DEMO ADMIN")
    b.pulsar("Clientes inactivos")
    assert "+34699000004" not in b.ultimo and "+346•• ••• 004" in b.ultimo
    b.escribir("responder +34699000004 hola, soy un desconocido")
    assert "no se puede" in b.ultimo
    assert all("desconocido" not in m for t, m in a.canal.enviados + b.canal.enviados
               if t == "+34699000004")
    # El alta de otro visitante no avisa a los visitantes admin
    c = _alta("+34699000006", "Fran Visitante")
    assert not [m for t, m in c.canal.enviados if t == "+34699000005"]
    # Las campañas no se envían de verdad
    from app.servicios import marketing
    from app.servicios.acciones import confirmar_accion
    from app.servicios.clientes import identificar

    with sesion(demo) as s:
        ctx = identificar(s, 1, "+34699000005")
        borr = marketing.borrador_campana(s, ctx, "¡Hola!")
        pend = marketing.enviar_campana(s, ctx, borr["campana_id"])
        res = confirmar_accion(s, ctx, pend["codigo"])
        assert res["enviados"] == 0 and not ctx.avisos
        assert s.get(Campana, borr["campana_id"]).estado == "descartada"  # type: ignore[union-attr]


def test_e_demo_03_reinicio_y_ocupacion() -> None:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    seed(eng, CFG, demo_clientes=30, demo_reservas=True)
    with sesion(eng) as s:
        assert (s.scalar(select(func.count()).select_from(Reserva)) or 0) > 20
        s.add(Cliente(estudio_id=1, nombre="Visitante", telefono="+34699999999"))
    reiniciar(eng, CFG)
    with sesion(eng) as s:
        assert s.scalar(select(Cliente).where(Cliente.telefono == "+34699999999")) is None
        assert s.scalar(select(func.count()).select_from(Cliente)) == 30
        from app.db.modelos import Staff

        assert s.scalar(select(Staff.telefono).where(Staff.rol == "admin")) == ADMIN
