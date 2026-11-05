"""Clientes, bonos, pagos, marketing y RGPD (RF-CLI-*, RF-COB-*, RF-MKT-*, RF-MEM-005)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.modelos import (
    Bono,
    Cliente,
    EstadoBono,
    EstadoCliente,
    EstadoPago,
    Memoria,
    Pago,
)
from app.integraciones.pagos import PagosFake
from app.servicios import bonos, clientes, marketing, pagos, reservas
from app.servicios.acciones import confirmar_accion
from app.servicios.base import Contexto, ErrorNegocio, Rol, SinPermiso
from tests.conftest import ADMIN, AHORA, LUNES, clase, ctx_de

Nuevo = Callable[..., Contexto]


def test_identificar_roles(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-ORQ-001"""
    assert ctx_de(s, ADMIN).rol == Rol.ADMIN
    assert ctx_de(s, "+34600000002").rol == Rol.INSTRUCTOR
    assert nuevo_cliente().rol == Rol.CLIENTE
    assert ctx_de(s, "+34000000000").rol == Rol.LEAD


def test_alta_de_lead(s: Session) -> None:
    """RF-CLI-001 / E-CLI-01"""
    ctx = ctx_de(s, "+34611222333")
    with pytest.raises(ErrorNegocio, match="privacidad"):
        clientes.alta_cliente(s, ctx, "Nuria", acepta_privacidad=False)
    r = clientes.alta_cliente(s, ctx, "Nuria Gómez", acepta_privacidad=True,
                              consent_marketing=True, objetivos="espalda")
    assert r["resultado"] == "ok" and ctx.rol == Rol.CLIENTE
    assert ctx.avisos and ctx.avisos[0].telefono == ADMIN
    assert ctx_de(s, "+34611222333").rol == Rol.CLIENTE


def test_lesion_requiere_consentimiento(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-CLI-004 + constitución §3.1"""
    ctx = nuevo_cliente()
    assert clientes.registrar_lesion(s, ctx, "rodilla")["resultado"] == "requiere_consentimiento"
    clientes.actualizar_consentimientos(s, ctx, consent_salud=True)
    assert clientes.registrar_lesion(s, ctx, "rodilla")["resultado"] == "ok"


def test_cliente_no_ve_datos_de_otro(s: Session, nuevo_cliente: Nuevo) -> None:
    """RNF-SEG-002 / E-MEM-02"""
    marta, pedro = nuevo_cliente(nombre="Marta"), nuevo_cliente(nombre="Pedro García")
    with pytest.raises(SinPermiso):
        clientes.buscar_cliente(s, marta, "Pedro")
    with pytest.raises(SinPermiso):
        clientes.ficha_cliente(s, marta, pedro.cliente_id)


def test_estado_bono_y_congelar(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-CLI-002, RF-CLI-007"""
    ctx = nuevo_cliente(usadas=4)
    assert bonos.estado_bono(s, ctx)["sesiones_disponibles"] == 6
    with pytest.raises(ErrorNegocio):
        bonos.congelar_bono(s, ctx, "viaje", 60)
    pend = bonos.congelar_bono(s, ctx, "lesión", 14)
    confirmar_accion(s, ctx, pend["codigo"])
    b = s.scalar(select(Bono).where(Bono.cliente_id == ctx.cliente_id))
    assert b is not None and b.estado == EstadoBono.CONGELADO
    c = clase(s, LUNES, 18, "reformer_inicial")
    with pytest.raises(ErrorNegocio, match="sesiones"):
        reservas.reservar_clase(s, ctx, c.id)
    assert bonos.mantenimiento_bonos(s, 1, AHORA.date() + timedelta(days=15))[
        "descongelados"] == 1


def test_compra_bono_solo_se_activa_con_webhook(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-COB-001/002 / E-COB-01, E-COB-02"""
    ctx = nuevo_cliente(bono=None)
    r = pagos.crear_enlace_pago(s, ctx, "bono10")
    assert r["enlace_pago"].startswith("http://test/dev/pagar/")
    assert bonos.estado_bono(s, ctx)["sesiones_disponibles"] == 0  # todavía nada

    pago = s.get(Pago, r["pago_id"])
    assert pago is not None and pago.proveedor_ref
    res = pagos.confirmar_pago_proveedor(s, pago.proveedor_ref, AHORA)
    assert res["resultado"] == "ok" and res["bono"]["sesiones_total"] == 10
    assert res["avisos"][0].telefono == ctx.telefono
    # Idempotente: Stripe reintenta webhooks
    assert pagos.confirmar_pago_proveedor(s, pago.proveedor_ref, AHORA)["resultado"] == (
        "ya_procesado")
    assert bonos.estado_bono(s, ctx)["sesiones_disponibles"] == 10


def test_pago_manual_y_devolucion_solo_admin(s: Session, nuevo_cliente: Nuevo,
                                              pagos_fake: PagosFake) -> None:
    """RF-COB-005 / E-COB-03, E-COB-04"""
    cli = nuevo_cliente(bono=None)
    with pytest.raises(SinPermiso):
        pagos.registrar_pago_manual(s, cli, cli.cliente_id, "bono5", "efectivo")
    admin = ctx_de(s, ADMIN)
    pend = pagos.registrar_pago_manual(s, admin, cli.cliente_id, "bono5", "efectivo")
    res = confirmar_accion(s, admin, pend["codigo"])
    assert res["bono"]["sesiones_total"] == 5

    pend = pagos.devolver_pago(s, admin, res["pago_id"], "se muda")
    confirmar_accion(s, admin, pend["codigo"])
    pago = s.get(Pago, res["pago_id"])
    assert pago is not None and pago.estado == EstadoPago.DEVUELTO
    assert pagos_fake.devoluciones == []  # efectivo: no se llama al proveedor
    b = s.scalar(select(Bono).where(Bono.pago_id == pago.id))
    assert b is not None and b.estado == EstadoBono.ANULADO
    caja = pagos.resumen_caja(s, admin, AHORA.date(), AHORA.date())
    assert caja["devuelto_eur"] == 85.0


def test_renovacion_e_impagos(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-COB-003"""
    ctx = nuevo_cliente(bono="mensual8")
    b = s.scalar(select(Bono).where(Bono.cliente_id == ctx.cliente_id))
    assert b is not None and b.caduca is not None
    sistema = Contexto.sistema(1, AHORA)
    assert pagos.generar_renovaciones(s, sistema, b.caduca) == 1
    assert sistema.avisos[0].plantilla == "pago_pendiente"
    assert pagos.generar_renovaciones(s, sistema, b.caduca) == 0  # no duplica
    pago = s.scalar(select(Pago).where(Pago.cliente_id == ctx.cliente_id))
    assert pago is not None
    hoy = pago.creado_en.date()
    sis = Contexto.sistema(1, AHORA)
    assert pagos.recordar_impagos(s, sis, hoy + timedelta(days=3), [0, 3, 7]) == 1
    sis = Contexto.sistema(1, AHORA)
    pagos.recordar_impagos(s, sis, hoy + timedelta(days=8), [0, 3, 7])
    assert [a.telefono for a in sis.avisos] == [ADMIN]


def test_campana_respeta_consentimiento_y_baja(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-MKT-004/005 / E-MKT-01, E-MKT-02"""
    si = nuevo_cliente(nombre="Con permiso", consent_marketing=True)
    nuevo_cliente(nombre="Sin permiso", consent_marketing=False)
    baja = nuevo_cliente(nombre="Se dará de baja", consent_marketing=True)
    admin = ctx_de(s, ADMIN)
    borr = marketing.borrador_campana(s, admin, "Taller de prenatal el sábado", nivel="inicial")
    assert borr["destinatarios"] == 2
    clientes.darse_de_baja_promos(s, baja)  # se da de baja antes del envío
    pend = marketing.enviar_campana(s, admin, borr["campana_id"])
    res = confirmar_accion(s, admin, pend["codigo"])
    assert res["enviados"] == 1
    assert [a.telefono for a in admin.avisos] == [si.telefono]


def test_recordatorios_no_se_repiten(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-MKT-001"""
    c = clase(s, LUNES, 18, "reformer_inicial")
    ctx = nuevo_cliente()
    reservas.reservar_clase(s, ctx, c.id)
    momento = c.inicio.replace(tzinfo=AHORA.tzinfo) - timedelta(hours=24, minutes=5)
    sis = Contexto.sistema(1, momento)
    assert marketing.recordatorios_clase(s, sis, [24, 2]) == 1
    assert marketing.recordatorios_clase(s, Contexto.sistema(1, momento), [24, 2]) == 0


def test_cumpleanos_e_informe(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-MKT-006, RF-MKT-007"""
    nuevo_cliente(consent_marketing=True, fecha_nac=date(1990, 11, 1))
    nuevo_cliente(consent_marketing=False, fecha_nac=date(1991, 11, 1))
    sis = Contexto.sistema(1, AHORA)
    assert marketing.felicitar_cumpleanos(s, sis, AHORA.date()) == 1
    inf = marketing.enviar_informe_semanal(s, Contexto.sistema(1, AHORA),
                                           LUNES + timedelta(days=7))
    assert inf["clases"] == 19


def test_borrar_mis_datos(s: Session, nuevo_cliente: Nuevo) -> None:
    """RF-MEM-005"""
    ctx = nuevo_cliente()
    s.add(Memoria(estudio_id=1, ambito="cliente", cliente_id=ctx.cliente_id,
                  ruta="preferencias.md", contenido="le gusta Laura"))
    reservas.reservar_clase(s, ctx, clase(s, LUNES, 18, "reformer_inicial").id)
    pend = clientes.borrar_mis_datos(s, ctx)
    confirmar_accion(s, ctx, pend["codigo"])
    cli = s.get(Cliente, ctx.cliente_id)
    assert cli is not None and cli.estado == EstadoCliente.BORRADO
    assert cli.telefono.startswith("borrado-")
    assert not s.scalars(select(Memoria).where(Memoria.cliente_id == ctx.cliente_id)).all()
    assert ctx_de(s, ctx.telefono).rol == Rol.LEAD
