"""Panel web del staff: escenarios E-PAN-01…06 (RF-PAN-*, RNF-SEG-004/005)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from app.db.modelos import (
    AccionPendiente,
    Bono,
    Clase,
    CodigoAcceso,
    Conversacion,
    EstadoAccion,
    EstadoConversacion,
    EstadoReserva,
    EventoAgente,
    Mensaje,
    Pago,
    Reserva,
    Staff,
)
from app.db.sesion import sesion
from app.integraciones.canal import set_canal
from app.panel import rutas
from app.panel.simulador import CanalWeb
from app.servicios.reservas import reservar_clase
from tests.conftest import ADMIN, AHORA, LAURA, LUNES, ctx_de

CARLOS = "+34600000003"


@pytest.fixture
def canal() -> Iterator[CanalWeb]:
    c = CanalWeb()
    set_canal(c)
    yield c
    set_canal(None)


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch) -> list:
    """Reloj controlable: reloj[0] es "ahora" para el panel."""
    actual = [AHORA]
    monkeypatch.setattr(rutas, "ahora", lambda: actual[0])
    return actual


@pytest.fixture
def web(entorno: Engine, canal: CanalWeb, reloj: list) -> TestClient:
    from app.main import app

    set_canal(canal)  # importar app.main pone su propio CanalWeb
    return TestClient(app)


def ultimo_codigo(canal: CanalWeb, tel: str) -> str:
    m = re.search(r"\*(\d{6})\*", canal.chats[tel][-1].texto)
    assert m
    return m.group(1)


def entrar(web: TestClient, canal: CanalWeb, tel: str) -> str:
    web.post("/panel/entrar", data={"telefono": tel})
    r = web.post("/panel/codigo", data={"telefono": tel, "codigo": ultimo_codigo(canal, tel)})
    assert r.status_code == 200 and "Semana del" in r.text
    token = web.cookies.get(rutas.COOKIE)
    assert token
    from app.panel.acceso import token_csrf

    return token_csrf(token)


# ------------------------------------------------------------------ E-PAN-01 / 02


def test_e_pan_01_entrar_con_codigo(web: TestClient, canal: CanalWeb, reloj: list) -> None:
    assert web.get("/panel").url.path == "/panel/entrar"  # sin sesión → a entrar
    entrar(web, canal, ADMIN)
    assert "Caja" in web.get("/panel").text  # el admin ve su menú completo


def test_e_pan_01_codigo_caducado(web: TestClient, canal: CanalWeb, reloj: list) -> None:
    web.post("/panel/entrar", data={"telefono": ADMIN})
    codigo = ultimo_codigo(canal, ADMIN)
    reloj[0] = AHORA + timedelta(minutes=11)
    r = web.post("/panel/codigo", data={"telefono": ADMIN, "codigo": codigo})
    assert "incorrecto o caducado" in r.text
    assert web.cookies.get(rutas.COOKIE) is None


def test_e_pan_01_cinco_intentos(web: TestClient, canal: CanalWeb) -> None:
    web.post("/panel/entrar", data={"telefono": ADMIN})
    bueno = ultimo_codigo(canal, ADMIN)
    malo = "000000" if bueno != "000000" else "111111"
    for _ in range(5):
        web.post("/panel/codigo", data={"telefono": ADMIN, "codigo": malo})
    r = web.post("/panel/codigo", data={"telefono": ADMIN, "codigo": bueno})
    assert "incorrecto o caducado" in r.text  # ya no vale ni el bueno


def test_e_pan_01_maximo_tres_codigos(web: TestClient, canal: CanalWeb,
                                      entorno: Engine) -> None:
    for _ in range(5):
        web.post("/panel/entrar", data={"telefono": "600 000 001"})  # sin prefijo también vale
    assert len(canal.chats[ADMIN]) == 3
    with sesion(entorno) as s:
        assert s.scalar(select(func.count()).select_from(CodigoAcceso)) == 3
        # el código no se guarda en claro
        assert all(len(c.codigo_hash) == 64 for c in s.scalars(select(CodigoAcceso)))


def test_e_pan_02_numero_que_no_es_staff(web: TestClient, canal: CanalWeb, entorno: Engine,
                                         nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    from urllib.parse import quote

    with sesion(entorno):
        tel_cliente = nuevo_cliente("Marta Ruiz").telefono
    r_cli = web.post("/panel/entrar", data={"telefono": tel_cliente})
    r_staff = web.post("/panel/entrar", data={"telefono": LAURA})

    def sin_tel(texto: str, tel: str) -> str:
        return texto.replace(quote(tel), "X").replace(tel, "X")

    # misma pantalla (solo cambia el número que se repite)
    assert sin_tel(r_cli.text, tel_cliente) == sin_tel(r_staff.text, LAURA)
    assert tel_cliente not in canal.chats
    with sesion(entorno) as s:
        assert s.scalar(select(func.count()).select_from(CodigoAcceso)) == 1


def test_sesion_caduca_y_staff_de_baja(web: TestClient, canal: CanalWeb, reloj: list,
                                       entorno: Engine) -> None:
    entrar(web, canal, LAURA)
    with sesion(entorno) as s:
        laura = s.scalar(select(Staff).where(Staff.telefono == LAURA))
        assert laura
        laura.activo = False
    assert web.get("/panel").url.path == "/panel/entrar"


def test_csrf_obligatorio(web: TestClient, canal: CanalWeb, entorno: Engine,
                         nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    with sesion(entorno):
        cli_id = nuevo_cliente().cliente_id
    entrar(web, canal, ADMIN)
    r = web.post(f"/panel/clientes/{cli_id}/nota", data={"texto": "hola", "csrf": "falso"})
    assert r.status_code == 400


# ------------------------------------------------------------------ E-PAN-03 / 04


def test_e_pan_03_instructora_solo_ve_lo_suyo(web: TestClient, canal: CanalWeb,
                                              entorno: Engine) -> None:
    entrar(web, canal, LAURA)
    with sesion(entorno) as s:
        laura = s.scalar(select(Staff).where(Staff.telefono == LAURA))
        carlos = s.scalar(select(Staff).where(Staff.telefono == CARLOS))
        assert laura and carlos
        de_carlos = s.scalars(select(Clase.id).where(Clase.instructor_id == carlos.id)).first()
    agenda = web.get(f"/panel?semana={LUNES.isoformat()}").text
    assert "Carlos" not in agenda and "Laura" in agenda
    assert "Caja" not in agenda
    r1 = web.get("/panel/caja")
    r2 = web.get(f"/panel/clases/{de_carlos}")
    assert r1.status_code == 403 and r2.status_code == 403
    assert "cobrado" not in r1.text.lower()


def test_e_pan_04_pasar_lista(web: TestClient, canal: CanalWeb, entorno: Engine,
                              nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    with sesion(entorno) as s:
        laura = s.scalar(select(Staff).where(Staff.telefono == LAURA))
        assert laura
        clase = s.scalars(select(Clase).where(Clase.instructor_id == laura.id)
                          .order_by(Clase.inicio)).first()
        assert clase
        clase_id = clase.id
    with sesion(entorno) as s:
        marta = nuevo_cliente("Marta Prueba")
        pedro = nuevo_cliente("Pedro Prueba")
        r_marta = reservar_clase(s, marta, clase_id)["reserva_id"]
        r_pedro = reservar_clase(s, pedro, clase_id)["reserva_id"]
    csrf = entrar(web, canal, LAURA)
    pagina = web.get(f"/panel/clases/{clase_id}").text
    assert "Marta Prueba" in pagina
    for rid, valor in ((r_marta, "1"), (r_pedro, "0")):
        web.post(f"/panel/reservas/{rid}/asistencia",
                 data={"clase_id": clase_id, "asistio": valor, "csrf": csrf})
    with sesion(entorno) as s:
        assert s.get(Reserva, r_marta).estado == EstadoReserva.ASISTIO  # type: ignore[union-attr]
        assert s.get(Reserva, r_pedro).estado == EstadoReserva.FALTA  # type: ignore[union-attr]
        eventos = list(s.scalars(select(EventoAgente).where(EventoAgente.agente == "panel")))
        assert [e.herramienta for e in eventos].count("marcar_asistencia") == 2
        assert eventos[0].entrada and eventos[0].entrada["staff_id"] == laura.id


# ------------------------------------------------------------------ E-PAN-05


def _pedir_pago(web: TestClient, csrf: str, cliente_id: int) -> str:
    r = web.post("/panel/caja/pago", data={"cliente_id": cliente_id, "producto": "bono5",
                                           "metodo": "efectivo", "importe": "85", "csrf": csrf},
                 follow_redirects=False)
    assert r.status_code == 303 and "/panel/confirmar/" in r.headers["location"]
    return r.headers["location"].split("/panel/confirmar/")[1].split("?")[0]


def test_e_pan_05_pago_en_efectivo_con_confirmacion(web: TestClient, canal: CanalWeb,
                                                    entorno: Engine, nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    from app.menu import motor

    with sesion(entorno) as s:
        assert motor.plan_estudio(s, 1) in ("ia", "basico")
        marta = nuevo_cliente("Marta Ruiz", bono=None)
        cliente_id = marta.cliente_id
        pagos_antes = s.scalar(select(func.count()).select_from(Pago))
    csrf = entrar(web, canal, ADMIN)
    codigo = _pedir_pago(web, csrf, cliente_id)
    with sesion(entorno) as s:  # todavía no se ha creado nada
        assert s.scalar(select(func.count()).select_from(Pago)) == pagos_antes
    pantalla = web.get(f"/panel/confirmar/{codigo}").text
    assert "Marta Ruiz" in pantalla and "efectivo" in pantalla
    r = web.post(f"/panel/confirmar/{codigo}", data={"decision": "si", "csrf": csrf,
                                                    "volver": "/panel/caja"})
    assert "Hecho" in r.text
    with sesion(entorno) as s:
        assert s.scalar(select(func.count()).select_from(Pago)) == pagos_antes + 1
        bono = s.scalar(select(Bono).where(Bono.cliente_id == cliente_id))
        assert bono and bono.estado == "activo"


def test_e_pan_05_confirmacion_caducada(web: TestClient, canal: CanalWeb, reloj: list,
                                        entorno: Engine, nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    with sesion(entorno) as s:
        cliente_id = nuevo_cliente("Marta Ruiz", bono=None).cliente_id
        pagos_antes = s.scalar(select(func.count()).select_from(Pago))
    csrf = entrar(web, canal, ADMIN)
    codigo = _pedir_pago(web, csrf, cliente_id)
    reloj[0] = AHORA + timedelta(minutes=16)
    r = web.post(f"/panel/confirmar/{codigo}", data={"decision": "si", "csrf": csrf})
    assert "caducado" in r.text
    with sesion(entorno) as s:
        assert s.scalar(select(func.count()).select_from(Pago)) == pagos_antes
        assert s.get(AccionPendiente, codigo).estado == EstadoAccion.EXPIRADA  # type: ignore[union-attr]


def test_instructora_no_puede_cobrar(web: TestClient, canal: CanalWeb, entorno: Engine,
                                     nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    with sesion(entorno) as s:
        cliente_id = nuevo_cliente("Marta Ruiz", bono=None).cliente_id
        acciones_antes = s.scalar(select(func.count()).select_from(AccionPendiente))
    csrf = entrar(web, canal, LAURA)
    r = web.post("/panel/caja/pago", data={"cliente_id": cliente_id, "producto": "bono5",
                                           "metodo": "efectivo", "csrf": csrf})
    assert r.status_code == 403
    with sesion(entorno) as s:
        assert s.scalar(select(func.count()).select_from(AccionPendiente)) == acciones_antes


# ------------------------------------------------------------------ E-PAN-06


def test_e_pan_06_contestar_conversacion_derivada(web: TestClient, canal: CanalWeb,
                                                  entorno: Engine, nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    from app.servicios import conversaciones

    with sesion(entorno) as s:
        marta = nuevo_cliente("Marta Ruiz")
        conversaciones.guardar_entrante(s, marta, "Me duele la lumbar", None)
        conversaciones.derivar_a_humano(s, marta, "consulta médica", False)
        conv_id = marta.conversacion_id
        tel = marta.telefono
    csrf = entrar(web, canal, ADMIN)
    lista = web.get("/panel/conversaciones").text
    assert "Esperan a una persona (1)" in lista and "Marta Ruiz" in lista
    web.post(f"/panel/conversaciones/{conv_id}/responder",
             data={"texto": "Hola Marta, te escribe Ana. ¿Desde cuándo te duele?",
                   "csrf": csrf})
    assert canal.chats[tel][-1].texto.startswith("Hola Marta, te escribe Ana")
    web.post(f"/panel/conversaciones/{conv_id}/reactivar", data={"csrf": csrf})
    with sesion(entorno) as s:
        conv = s.get(Conversacion, conv_id)
        assert conv and conv.estado == EstadoConversacion.BOT
        textos = [m.contenido for m in s.scalars(select(Mensaje).where(
            Mensaje.conversacion_id == conv_id))]
        assert any(t.startswith("Hola Marta, te escribe Ana") for t in textos)


# ------------------------------------------------------------------ resto de pantallas


def test_admin_recorre_todas_las_pantallas(web: TestClient, canal: CanalWeb,
                                           entorno: Engine, nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    with sesion(entorno) as s:
        cli_id = nuevo_cliente().cliente_id
    entrar(web, canal, ADMIN)
    with sesion(entorno) as s:
        clase_id = s.scalars(select(Clase.id)).first()
    for url in ["/panel", f"/panel?semana={LUNES.isoformat()}", "/panel/clientes?q=a",
                f"/panel/clientes/{cli_id}", f"/panel/clases/{clase_id}",
                f"/panel/clases/{clase_id}/editar", "/panel/clases/nueva",
                "/panel/caja", "/panel/caja?periodo=mes", "/panel/campanas",
                "/panel/campanas?inactivos=21", "/panel/conversaciones", "/panel/informes"]:
        r = web.get(url)
        assert r.status_code == 200, url


def test_crear_y_cancelar_clase(web: TestClient, canal: CanalWeb, entorno: Engine,
                                nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    csrf = entrar(web, canal, ADMIN)
    r = web.post("/panel/clases/nueva", data={
        "tipo": "reformer_inicial", "fecha": (LUNES + timedelta(days=2)).isoformat(),
        "hora": "21:30", "instructor": "Laura", "aforo": "4", "csrf": csrf},
        follow_redirects=False)
    assert r.status_code == 303, r.text
    clase_id = int(r.headers["location"].split("/panel/clases/")[1].split("?")[0])
    with sesion(entorno) as s:
        marta = nuevo_cliente("Marta Ruiz")
        reservar_clase(s, marta, clase_id)
        tel = marta.telefono
    r = web.post(f"/panel/clases/{clase_id}/cancelar",
                 data={"motivo": "avería del reformer", "csrf": csrf}, follow_redirects=False)
    codigo = r.headers["location"].split("/panel/confirmar/")[1].split("?")[0]
    web.post(f"/panel/confirmar/{codigo}", data={"decision": "si", "csrf": csrf})
    with sesion(entorno) as s:
        assert s.get(Clase, clase_id).estado == "cancelada"  # type: ignore[union-attr]
    assert "avería del reformer" in canal.chats[tel][-1].texto


def test_campana_respeta_consentimiento(web: TestClient, canal: CanalWeb,
                                        entorno: Engine, nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    with sesion(entorno):
        si = nuevo_cliente("Con Permiso", consent_marketing=True)
        no = nuevo_cliente("Sin Permiso", consent_marketing=False)
        tel_si, tel_no = si.telefono, no.telefono
    csrf = entrar(web, canal, ADMIN)
    r = web.post("/panel/campanas", data={"texto": "Taller el sábado", "csrf": csrf},
                 follow_redirects=False)
    codigo = r.headers["location"].split("/panel/confirmar/")[1].split("?")[0]
    web.post(f"/panel/confirmar/{codigo}", data={"decision": "si", "csrf": csrf})
    assert "Taller el sábado" in canal.chats[tel_si][-1].texto
    assert tel_no not in canal.chats


def test_simulador_de_whatsapp(web: TestClient, canal: CanalWeb) -> None:
    r = web.post("/dev/chat", data={"tel": ADMIN, "texto": "menú"})
    assert r.status_code == 200 and "Simulador" in r.text
    assert canal.chats[ADMIN][0].de_mi and not canal.chats[ADMIN][-1].de_mi
    assert canal.chats[ADMIN][-1].opciones  # el menú trae botones o lista


def test_contexto_de_staff(entorno: Engine) -> None:
    with sesion(entorno) as s:
        assert ctx_de(s, ADMIN).staff_id is not None


def test_rf_pan_011_demo(web: TestClient, canal: CanalWeb, entorno: Engine,
                         monkeypatch: pytest.MonkeyPatch, nuevo_cliente) -> None:  # type: ignore[no-untyped-def]
    from app.servicios import conversaciones
    from config.settings import get_settings

    monkeypatch.setattr(get_settings(), "demo", True)
    with sesion(entorno) as s:
        marta = nuevo_cliente("Marta Ruiz")
        conversaciones.guardar_entrante(s, marta, "hola", None)
        conv_id, tel = marta.conversacion_id, marta.telefono
    csrf = entrar(web, canal, ADMIN)
    pagina = web.get(f"/panel/conversaciones/{conv_id}").text
    assert tel not in pagina and "••" in pagina  # teléfono enmascarado
    r = web.post(f"/panel/conversaciones/{conv_id}/responder",
                 data={"texto": "hola", "csrf": csrf})
    assert "En la demo no se puede escribir" in r.text
    assert tel not in canal.chats
