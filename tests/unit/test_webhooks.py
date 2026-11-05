"""Webhooks de WhatsApp y pagos (RNF-SEG-001, RNF-FIA-001, RF-COB-002, diseño §8)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app import main
from app.db import sesion as modulo_sesion
from app.db.modelos import Bono, Cliente, Pago
from app.db.sesion import sesion
from app.integraciones.canal import CanalConsola, firma_valida, set_canal
from app.servicios.base import Contexto, Rol
from app.servicios.pagos import crear_enlace_pago
from config.settings import get_settings
from tests.conftest import AHORA

SECRETO = "s3cr3t"


@pytest.fixture
def cliente_http(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(modulo_sesion, "get_engine", lambda url=None: engine)
    cfg = get_settings()
    monkeypatch.setattr(cfg, "whatsapp_app_secret", SECRETO)
    monkeypatch.setattr(cfg, "whatsapp_verify_token", "verifica")
    monkeypatch.setattr(cfg, "pagos_proveedor", "fake")
    return TestClient(main.app)


def _firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()


def test_firma() -> None:
    assert firma_valida(b"x", _firmar(b"x"), SECRETO)
    assert not firma_valida(b"x", _firmar(b"y"), SECRETO)
    assert not firma_valida(b"x", None, SECRETO)
    assert not firma_valida(b"x", _firmar(b"x"), "")


def test_verificacion_meta(cliente_http: TestClient) -> None:
    r = cliente_http.get("/webhook/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "verifica", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"
    r = cliente_http.get("/webhook/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "mal", "hub.challenge": "42"})
    assert r.status_code == 403


def test_webhook_rechaza_sin_firma_y_encola(cliente_http: TestClient,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    encolados: list[dict[str, Any]] = []

    async def falso(m: dict[str, Any]) -> None:
        encolados.append(m)

    monkeypatch.setattr(main, "encolar_mensaje", falso)
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.A", "from": "34611000111", "type": "text", "text": {"body": "Hola"}},
        {"id": "wamid.B", "from": "34611000111", "type": "audio", "audio": {"id": "m1"}},
        {"id": "wamid.C", "from": "34611000111", "type": "image",
         "image": {"id": "m2", "caption": "justificante"}},
    ]}}]}]}
    cuerpo = json.dumps(payload).encode()
    assert cliente_http.post("/webhook/whatsapp", content=cuerpo).status_code == 401
    r = cliente_http.post("/webhook/whatsapp", content=cuerpo,
                          headers={"X-Hub-Signature-256": _firmar(cuerpo),
                                   "Content-Type": "application/json"})
    assert r.json() == {"encolados": 3}
    assert encolados[0] == {"wa_message_id": "wamid.A", "telefono": "+34611000111",
                            "tipo": "text", "texto": "Hola"}
    assert encolados[1]["media_id"] == "m1"
    assert encolados[2]["texto"] == "justificante"


def test_pago_fake_activa_bono_y_avisa(cliente_http: TestClient, engine: Engine) -> None:
    """RF-COB-002: el pago llega por el webhook del proveedor, no por el agente."""
    canal = CanalConsola(silencioso=True)
    set_canal(canal)
    try:
        with sesion(engine) as s:
            cli = Cliente(estudio_id=1, nombre="Nuria", telefono="+34611999888")
            s.add(cli)
            s.flush()
            ctx = Contexto(estudio_id=1, telefono=cli.telefono, rol=Rol.CLIENTE, cliente_id=cli.id,
                           ahora=AHORA)
            enlace = crear_enlace_pago(s, ctx, "bono5")["enlace_pago"]
        ruta = enlace.replace("http://test", "")
        assert cliente_http.post(ruta).status_code == 200
        assert cliente_http.post(ruta).status_code == 200  # reintento idempotente
        with sesion(engine) as s:
            assert len(s.scalars(select(Bono)).all()) == 1
            pago = s.scalars(select(Pago)).one()
            assert pago.estado == "pagado"
        assert len([t for t, _ in canal.enviados if t == "+34611999888"]) == 1
        assert cliente_http.post("/dev/pagar/no-existe").status_code == 404
    finally:
        set_canal(None)
