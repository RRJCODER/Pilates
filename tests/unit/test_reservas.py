"""Reservas, cancelaciones, lista de espera y clases (RF-RES-*)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.modelos import Bono, EstadoClase, EstadoEspera, ListaEspera
from app.servicios import clases, lista_espera, reservas
from app.servicios.acciones import confirmar_accion
from app.servicios.base import Contexto, ErrorNegocio, SinPermiso
from tests.conftest import ADMIN, AHORA, LAURA, LUNES, MARTA, clase, ctx_de

# Lunes 2/11 19:00 Madrid = 18:00 UTC → Reformer Inicial con Marta (aforo 6)
H_REFORMER_19 = 18

Nuevo = Callable[..., Contexto]


def test_buscar_clases_muestra_plazas(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-RES-001"""
    ctx = nuevo_cliente()
    res = clases.buscar_clases(s, ctx, AHORA, AHORA + timedelta(days=2), modalidad="reformer")
    assert res and all(c["modalidad"] == "reformer" for c in res)
    lunes19 = [c for c in res if c["hora"] == "19:00"][0]
    assert lunes19["plazas_libres"] == 6
    assert lunes19["instructor"] == "Marta"


def test_reserva_normal_descuenta_sesion(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-RES-002 / E-RES-01"""
    ctx = nuevo_cliente(usadas=6)
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")
    r = reservas.reservar_clase(s, ctx, c.id)
    assert r["resultado"] == "ok"
    assert r["sesiones_restantes_bono"] == 3
    with pytest.raises(ErrorNegocio, match="Ya tienes plaza"):
        reservas.reservar_clase(s, ctx, c.id)


def test_sin_bono_no_reserva(s: Session, nuevo_cliente: Nuevo) -> None:
    ctx = nuevo_cliente(bono=None)
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")
    with pytest.raises(ErrorNegocio, match="sesiones disponibles"):
        reservas.reservar_clase(s, ctx, c.id)


def test_lead_no_puede_reservar(s: Session) -> None:
    ctx = ctx_de(s, "+34999999999")
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")
    with pytest.raises(SinPermiso):
        reservas.reservar_clase(s, ctx, c.id)


def _llenar(s: Session, nuevo_cliente: Nuevo, clase_id: int, n: int) -> list[Contexto]:
    out = []
    for i in range(n):
        ctx = nuevo_cliente(nombre=f"Cliente {i}")
        assert reservas.reservar_clase(s, ctx, clase_id)["resultado"] == "ok"
        out.append(ctx)
    return out


def test_clase_llena_ofrece_alternativas_y_lista(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-RES-003 / E-RES-02"""
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")
    _llenar(s, nuevo_cliente, c.id, 6)
    ctx = nuevo_cliente(nombre="La séptima")
    r = reservas.reservar_clase(s, ctx, c.id)
    assert r["resultado"] == "clase_llena"
    assert 0 < len(r["alternativas"]) <= 2
    assert all(a["modalidad"] == "reformer" and a["plazas_libres"] > 0 for a in r["alternativas"])
    assert lista_espera.unirse(s, ctx, c.id)["personas_delante"] == 0


def test_cancelacion_a_tiempo_y_tardia(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-RES-005 / E-RES-03"""
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")
    ctx = nuevo_cliente()
    r = reservas.reservar_clase(s, ctx, c.id)
    res = reservas.cancelar_reserva(s, ctx, r["reserva_id"])
    assert res["sesion_devuelta"] is True

    r = reservas.reservar_clase(s, ctx, c.id)  # se puede volver a reservar
    tarde = ctx_de(s, ctx.telefono, ahora=c.inicio.replace(tzinfo=AHORA.tzinfo)
                   - timedelta(hours=3))
    res = reservas.cancelar_reserva(s, tarde, r["reserva_id"])
    assert res["sesion_devuelta"] is False
    bono = s.scalar(select(Bono).where(Bono.cliente_id == ctx.cliente_id))
    assert bono is not None and bono.sesiones_usadas == 1


def test_no_puede_cancelar_reserva_ajena(s: Session, nuevo_cliente: Nuevo) -> None:
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")
    a, b = nuevo_cliente(nombre="A"), nuevo_cliente(nombre="B")
    r = reservas.reservar_clase(s, a, c.id)
    with pytest.raises(ErrorNegocio):
        reservas.cancelar_reserva(s, b, r["reserva_id"])


def test_plaza_liberada_se_ofrece_y_caduca(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-RES-004 / E-RES-04"""
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")
    dentro = _llenar(s, nuevo_cliente, c.id, 6)
    pedro, ana = nuevo_cliente(nombre="Pedro"), nuevo_cliente(nombre="Ana")
    lista_espera.unirse(s, pedro, c.id)
    lista_espera.unirse(s, ana, c.id)

    reserva = reservas.mis_reservas(s, dentro[0])[0]
    reservas.cancelar_reserva(s, dentro[0], reserva["reserva_id"])
    assert [a.telefono for a in dentro[0].avisos] == [pedro.telefono]
    assert dentro[0].avisos[0].plantilla == "plaza_liberada"

    # Mientras la oferta está viva la plaza cuenta como ocupada
    assert reservas.reservar_clase(s, nuevo_cliente(), c.id)["resultado"] == "clase_llena"

    # Pedro no responde: caduca y se ofrece a Ana
    luego = ctx_de(s, ADMIN, ahora=AHORA + timedelta(hours=3))
    assert lista_espera.expirar_ofertas(s, luego) == 1
    assert [a.telefono for a in luego.avisos] == [ana.telefono]
    ana_luego = ctx_de(s, ana.telefono, ahora=AHORA + timedelta(hours=3, minutes=10))
    assert reservas.aceptar_plaza_lista_espera(s, ana_luego, c.id)["resultado"] == "ok"
    estados = {e.cliente_id: e.estado for e in s.scalars(select(ListaEspera))}
    assert estados[pedro.cliente_id] == EstadoEspera.EXPIRADA
    assert estados[ana.cliente_id] == EstadoEspera.ACEPTADA


def test_reserva_recurrente(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-RES-006: lunes a las 19:00 durante 2 semanas (el seed genera 2 semanas)."""
    ctx = nuevo_cliente()
    r = reservas.reservar_recurrente(s, ctx, dia_semana=0, hora="19:00", semanas=3)
    assert r["hechas"] == 2
    assert r["reservas"][2]["resultado"] == "no_hay_clase"


def test_cancelar_clase_requiere_confirmacion_y_devuelve_sesiones(
        s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-RES-007 + diseño §7"""
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")
    alumnos = _llenar(s, nuevo_cliente, c.id, 2)
    admin = ctx_de(s, ADMIN)
    pend = clases.cancelar_clase(s, admin, c.id, "profe enferma")
    assert pend["resultado"] == "requiere_confirmacion"
    assert c.estado == EstadoClase.PROGRAMADA

    with pytest.raises(ErrorNegocio, match="otra persona"):
        confirmar_accion(s, alumnos[0], pend["codigo"])
    res = confirmar_accion(s, admin, pend["codigo"])
    assert res["clientes_avisados"] == 2
    assert c.estado == EstadoClase.CANCELADA
    assert {a.telefono for a in admin.avisos} == {a.telefono for a in alumnos}
    for b in s.scalars(select(Bono).where(Bono.cliente_id.in_([a.cliente_id for a in alumnos]))):
        assert b.sesiones_usadas == 0


def test_confirmacion_caduca(s: Session) -> None:
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")
    admin = ctx_de(s, ADMIN)
    pend = clases.cancelar_clase(s, admin, c.id, "x")
    tarde = ctx_de(s, ADMIN, ahora=AHORA + timedelta(minutes=16))
    with pytest.raises(ErrorNegocio, match="caducado"):
        confirmar_accion(s, tarde, pend["codigo"])


def test_instructor_solo_sus_clases(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-RES-008 + RF-CLI-004 (lesiones visibles para el instructor)"""
    c = clase(s, LUNES, H_REFORMER_19, "reformer_inicial")  # es de Marta
    ctx = nuevo_cliente(consent_salud=True)
    reservas.reservar_clase(s, ctx, c.id)
    from app.servicios.clientes import registrar_lesion

    registrar_lesion(s, ctx, "Molestias en lumbar")
    with pytest.raises(SinPermiso):
        clases.asistentes_clase(s, ctx_de(s, LAURA), c.id)
    marta = ctx_de(s, MARTA)
    info = clases.asistentes_clase(s, marta, c.id)
    assert info["asistentes"][0]["lesiones"] == ["Molestias en lumbar"]
    r = clases.marcar_asistencia(s, marta, info["asistentes"][0]["reserva_id"], True)
    assert r["estado"] == "asistio"
