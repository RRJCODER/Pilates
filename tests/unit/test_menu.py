"""Plan Básico y motor de menús (RF-PLAN-*, RF-BAS-*; escenarios E-BAS-*, E-PLAN-01)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select

from app.agentes import claude
from app.conversacion import Resultado, procesar_mensaje
from app.db import sesion as modulo_sesion
from app.db.modelos import (
    AccionPendiente,
    Bono,
    Clase,
    Cliente,
    EstadoBono,
    EstadoClase,
    EstadoReserva,
    Estudio,
    Producto,
    Reserva,
)
from app.db.sesion import sesion
from app.integraciones.canal import CanalConsola
from tests.conftest import ADMIN, AHORA, LUNES, MARTA

TEL = "+34611000222"


class SinIA:
    """Cliente de Claude que falla si se usa: el plan Básico no debe llamarlo."""

    llamadas = 0

    @property
    def beta(self) -> Any:
        SinIA.llamadas += 1
        raise AssertionError("Se ha llamado a la IA")


@pytest.fixture
def entorno(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    monkeypatch.setattr(modulo_sesion, "get_engine", lambda url=None: engine)
    SinIA.llamadas = 0
    claude.set_cliente(SinIA())  # type: ignore[arg-type]
    yield engine
    claude.set_cliente(None)


def poner_plan(engine: Engine, plan: str) -> None:
    with sesion(engine) as s:
        e = s.get(Estudio, 1)
        assert e is not None
        e.config = {**e.config, "estudio": {**e.config["estudio"], "plan": plan}}


def crear_cliente(engine: Engine, tel: str = TEL) -> None:
    with sesion(engine) as s:
        c = Cliente(estudio_id=1, nombre="Lucía Pérez", telefono=tel)
        s.add(c)
        s.flush()
        p = s.scalar(select(Producto).where(Producto.clave == "bono10"))
        assert p
        s.add(Bono(estudio_id=1, cliente_id=c.id, producto_id=p.id, sesiones_total=10,
                   sesiones_usadas=0, inicio=AHORA.date(),
                   caduca=AHORA.date() + timedelta(days=90), estado=EstadoBono.ACTIVO))


class Chat:
    """Simula a una persona pulsando botones en WhatsApp."""

    def __init__(self, tel: str, ahora: datetime = AHORA) -> None:
        self.tel, self.ahora, self.n = tel, ahora, 0
        self.canal = CanalConsola(silencioso=True)

    def escribir(self, texto: str) -> Resultado:
        self.n += 1
        return procesar_mensaje(1, self.tel, texto, f"{self.tel}-{self.n}", canal=self.canal,
                                ahora=self.ahora)

    def opciones(self) -> list[tuple[str, str]]:
        return self.canal.opciones.get(self.tel, [])

    def pulsar(self, contiene: str) -> Resultado:
        for rid, titulo in self.opciones():
            if contiene.lower() in titulo.lower():
                self.n += 1
                return procesar_mensaje(1, self.tel, titulo, f"{self.tel}-{self.n}",
                                        canal=self.canal, ahora=self.ahora, respuesta_id=rid)
        raise AssertionError(f"No hay opción «{contiene}» en {self.opciones()}")

    @property
    def ultimo(self) -> str:
        """Último mensaje enviado a esta persona (no los avisos a terceros)."""
        return [m for t, m in self.canal.enviados if t == self.tel][-1]


def test_e_bas_01_reservar_con_botones(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    crear_cliente(entorno)
    chat = Chat(TEL)
    res = chat.escribir("hola")
    assert res.menu and "Reservar" in chat.ultimo
    chat.pulsar("Reservar clase")
    chat.pulsar("Mañana")  # lunes 2/11
    chat.pulsar("19:00 Reformer")
    assert "Reservado" in chat.ultimo and "19:00" in chat.ultimo
    with sesion(entorno) as s:
        assert len(s.scalars(select(Reserva)).all()) == 1
        b = s.scalars(select(Bono)).one()
        assert b.sesiones_usadas == 1
    assert SinIA.llamadas == 0


def test_clase_llena_ofrece_lista_de_espera(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    crear_cliente(entorno)
    with sesion(entorno) as s:
        c = s.scalar(select(Clase).where(Clase.inicio == datetime(2026, 11, 2, 18, tzinfo=UTC)))
        assert c
        c.aforo = 0
    chat = Chat(TEL)
    chat.escribir("menu")
    chat.pulsar("Reservar clase")
    chat.pulsar("Mañana")
    chat.pulsar("19:00 Reformer")
    assert "completa" in chat.ultimo
    chat.pulsar("Lista de espera")
    assert "lista de espera" in chat.ultimo


def test_e_bas_02_cancelar_con_aviso_de_politica(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    crear_cliente(entorno)
    chat = Chat(TEL)
    chat.escribir("menu")
    chat.pulsar("Reservar clase")
    chat.pulsar("Mañana")
    chat.pulsar("19:00 Reformer")
    # 3 horas antes de la clase
    tarde = Chat(TEL, datetime(2026, 11, 2, 15, tzinfo=UTC))
    tarde.n = 100
    tarde.escribir("menu")
    tarde.pulsar("Mis reservas")
    tarde.pulsar("Lunes")
    assert "no* se devuelve" in tarde.ultimo
    with sesion(entorno) as s:
        assert s.scalars(select(Reserva)).one().estado == EstadoReserva.CONFIRMADA
    tarde.pulsar("Sí, cancelar")
    assert "no se devuelve" in tarde.ultimo
    with sesion(entorno) as s:
        assert s.scalars(select(Reserva)).one().estado == EstadoReserva.CANCELADA


def test_e_bas_03_alta_por_menu(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    chat = Chat("+34699888777")
    chat.escribir("Hola")
    chat.pulsar("Darme de alta")
    chat.escribir("Nuria López")
    chat.pulsar("Acepto")
    chat.pulsar("No, gracias")
    with sesion(entorno) as s:
        c = s.scalar(select(Cliente).where(Cliente.telefono == "+34699888777"))
        assert c is not None and c.nombre == "Nuria López" and c.consent_marketing is False
    assert "Ver catálogo" in chat.ultimo or "comprar" in chat.ultimo.lower()


def test_alta_rechazando_privacidad_no_crea_cliente(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    chat = Chat("+34699888000")
    chat.escribir("hola")
    chat.pulsar("Darme de alta")
    chat.escribir("Ana")
    chat.pulsar("No acepto")
    with sesion(entorno) as s:
        assert s.scalar(select(Cliente).where(Cliente.telefono == "+34699888000")) is None


def test_e_bas_04_texto_libre_en_basico(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    crear_cliente(entorno)
    chat = Chat(TEL)
    res = chat.escribir("¿puedo ir el jueves?")
    assert res.menu and "Te atiendo con este menú" in chat.canal.enviados[0][1]
    assert SinIA.llamadas == 0


def test_e_plan_01_plan_ia_botones_sin_ia(entorno: Engine) -> None:
    poner_plan(entorno, "ia")
    crear_cliente(entorno)
    chat = Chat(TEL)
    chat.escribir("MENÚ")
    chat.pulsar("Mi bono")
    assert "te quedan *10*" in chat.ultimo
    assert SinIA.llamadas == 0
    # El texto libre sí va a la IA (aquí falla a propósito → mensaje de cortesía)
    res = chat.escribir("¿Qué me recomiendas para la espalda?")
    assert not res.menu and SinIA.llamadas == 1


def test_si_en_plan_ia_solo_si_hay_algo_que_confirmar(entorno: Engine) -> None:
    poner_plan(entorno, "ia")
    crear_cliente(entorno)
    res = Chat(TEL).escribir("sí")
    assert not res.menu  # sin nada pendiente, el "sí" es para la IA


def test_admin_cancela_clase_con_confirmacion(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    chat = Chat(ADMIN)
    chat.escribir("menu")
    chat.pulsar("Cancelar una clase")
    chat.pulsar("19:00 Reformer")
    assert "Confirmar" in str(chat.opciones())
    with sesion(entorno) as s:
        assert s.scalars(select(AccionPendiente)).one()
    chat.pulsar("Confirmar")
    with sesion(entorno) as s:
        c = s.scalar(select(Clase).where(Clase.inicio == datetime(2026, 11, 2, 18, tzinfo=UTC)))
        assert c is not None and c.estado == EstadoClase.CANCELADA


def test_cliente_no_puede_usar_ids_de_admin(entorno: Engine) -> None:
    """Un id fabricado a mano no salta los permisos (RNF-SEG-002)."""
    poner_plan(entorno, "basico")
    crear_cliente(entorno)
    with sesion(entorno) as s:
        c = s.scalar(select(Clase).where(Clase.inicio == datetime(2026, 11, 2, 18, tzinfo=UTC)))
        assert c
        cid = c.id
    for rid in ("ad:caja", f"ad:cc:{cid}", "st:clases:0", f"st:clase:{cid}"):
        procesar_mensaje(1, TEL, "x", f"hack-{rid}", canal=CanalConsola(silencioso=True),
                         ahora=AHORA, respuesta_id=rid)
    with sesion(entorno) as s:
        assert not s.scalars(select(AccionPendiente)).all()


def test_instructor_marca_asistencia(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    crear_cliente(entorno)
    alumno = Chat(TEL)
    alumno.escribir("menu")
    alumno.pulsar("Reservar clase")
    alumno.pulsar("Mañana")
    alumno.pulsar("19:00 Reformer")
    profe = Chat(MARTA, datetime(2026, 11, 2, 8, tzinfo=UTC))
    profe.escribir("menu")
    profe.pulsar("clases de hoy")
    profe.pulsar("19:00")
    profe.pulsar("Lucía")
    profe.pulsar("Vino")
    with sesion(entorno) as s:
        assert s.scalars(select(Reserva)).one().estado == EstadoReserva.ASISTIO


def test_admin_responde_y_reactiva(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    crear_cliente(entorno)
    cliente = Chat(TEL)
    cliente.escribir("menu")
    cliente.pulsar("Hablar con el estudio")
    admin = Chat(ADMIN)
    admin.escribir(f"responder {TEL} Hola Lucía, soy Ana. ¿En qué te ayudo?")
    assert (TEL, "Hola Lucía, soy Ana. ¿En qué te ayudo?") in admin.canal.enviados
    admin.escribir(f"reactivar {TEL}")
    assert cliente.escribir("menu").menu  # el bot vuelve a atender


def test_baja(entorno: Engine) -> None:
    poner_plan(entorno, "basico")
    crear_cliente(entorno)
    with sesion(entorno) as s:
        c = s.scalar(select(Cliente).where(Cliente.telefono == TEL))
        assert c
        c.consent_marketing = True
    Chat(TEL).escribir("Baja")
    with sesion(entorno) as s:
        c = s.scalar(select(Cliente).where(Cliente.telefono == TEL))
        assert c is not None and c.consent_marketing is False


def test_lunes_es_manana() -> None:
    assert AHORA.date() + timedelta(days=1) == LUNES
