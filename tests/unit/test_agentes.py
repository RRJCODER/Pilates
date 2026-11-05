"""Cableado de agentes con Claude simulado (transporte HTTP falso).

Comprueba: delegación orquestador → subagente → herramienta → base de datos,
parámetros de la API (effort, fallbacks, caching), herramientas visibles por rol,
memoria aislada por cliente, deduplicación y mensaje de error (RNF-FIA-001/002).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import anthropic
import httpx2
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.agentes import claude
from app.agentes.memoria import MemoriaDB
from app.conversacion import MENSAJE_ERROR, procesar_mensaje
from app.db import sesion as modulo_sesion
from app.db.modelos import Bono, EventoAgente, Memoria, Mensaje, Reserva
from app.db.sesion import sesion
from app.integraciones.canal import CanalConsola
from app.servicios.base import Contexto
from tests.conftest import ADMIN, AHORA, LUNES, clase

Respuesta = Callable[[dict[str, Any]], dict[str, Any]]


def _msg(contenido: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {"id": "msg_x", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": contenido, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20}}


def texto(t: str) -> dict[str, Any]:
    return _msg([{"type": "text", "text": t}])


def herramienta(nombre: str, entrada: dict[str, Any], id_: str = "tu_1") -> dict[str, Any]:
    return _msg([{"type": "tool_use", "id": id_, "name": nombre, "input": entrada}], "tool_use")


class ClaudeFalso:
    """Responde según el agente (se distingue por las herramientas de la petición)."""

    def __init__(self) -> None:
        self.guiones: dict[str, list[Respuesta]] = {}
        self.peticiones: list[dict[str, Any]] = []
        self.hilos: dict[str, str] = {}
        self.contador = 0

    def agente(self, cuerpo: dict[str, Any]) -> str:
        nombres = {t.get("name") for t in cuerpo.get("tools", [])}
        if "delegar_reservas" in nombres:
            return "orquestador"
        for sub, marca in [("reservas", "reservar_clase"), ("clientes", "estado_bono"),
                           ("cobros", "crear_enlace_pago"), ("marketing", "segmentar")]:
            if marca in nombres:
                return sub
        if "darse_de_baja_promos" in nombres:
            return "marketing"
        return "otro"

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        cuerpo = json.loads(request.content)
        self.peticiones.append(cuerpo)
        # El Tool Runner usa message threads: las peticiones siguientes solo llevan
        # los mensajes nuevos y el id del mensaje anterior.
        hilo = (cuerpo.get("thread") or {}).get("previous_message_id")
        agente = self.hilos[hilo] if hilo else self.agente(cuerpo)
        respuesta = self.guiones[agente].pop(0)(cuerpo)
        self.contador += 1
        respuesta["id"] = f"msg_{self.contador}"
        self.hilos[respuesta["id"]] = agente
        return httpx2.Response(200, json=respuesta)


@pytest.fixture
def falso(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[ClaudeFalso]:
    from config.settings import get_settings

    monkeypatch.setattr(modulo_sesion, "get_engine", lambda url=None: engine)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test")
    f = ClaudeFalso()
    claude.set_cliente(anthropic.Anthropic(
        api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(f)),
        max_retries=0))
    yield f
    claude.set_cliente(None)


def _crear_cliente(engine: Engine) -> str:
    from app.db.modelos import Cliente, EstadoBono, Producto

    with sesion(engine) as s:
        cli = Cliente(estudio_id=1, nombre="Lucía", telefono="+34611000111")
        s.add(cli)
        s.flush()
        p = s.scalar(select(Producto).where(Producto.clave == "bono10"))
        assert p is not None
        s.add(Bono(estudio_id=1, cliente_id=cli.id, producto_id=p.id, sesiones_total=10,
                   sesiones_usadas=6, inicio=AHORA.date(), caduca=LUNES.replace(year=2027),
                   estado=EstadoBono.ACTIVO))
    return "+34611000111"


def test_reserva_de_punta_a_punta(engine: Engine, falso: ClaudeFalso) -> None:
    """E-RES-01 con el agente: orquestador → reservas → reservar_clase → BD."""
    tel = _crear_cliente(engine)
    with sesion(engine) as s:
        clase_id = clase(s, LUNES, 18, "reformer_inicial").id

    falso.guiones["orquestador"] = [
        lambda c: herramienta("delegar_reservas",
                              {"tarea": "Reservar reformer lunes 2026-11-02 19:00"}),
        lambda c: texto("¡Hecho! Lunes a las 19:00 con Marta. Te quedan 3 sesiones 💪"),
    ]
    falso.guiones["reservas"] = [
        lambda c: herramienta("reservar_clase", {"clase_id": clase_id}),
        lambda c: texto(f"Reservada clase_id={clase_id}. Quedan 3 sesiones."),
    ]
    canal = CanalConsola(silencioso=True)
    res = procesar_mensaje(1, tel, "Apúntame al reformer del lunes a las 7", "wamid.1",
                           canal=canal, ahora=AHORA)

    assert res.respuesta and "19:00" in res.respuesta
    assert canal.enviados[0] == (tel, res.respuesta)
    with sesion(engine) as s:
        assert len(s.scalars(select(Reserva)).all()) == 1
        bono = s.scalars(select(Bono)).first()
        assert bono is not None and bono.sesiones_usadas == 7
        herrs = [e.herramienta for e in s.scalars(select(EventoAgente))]
        assert "reservar_clase" in herrs
        assert len(s.scalars(select(Mensaje)).all()) == 2  # entrante + respuesta

    orq = falso.peticiones[0]
    assert orq["model"] == "claude-opus-5-5"
    assert orq["output_config"] == {"effort": "medium"}
    assert orq["fallbacks"] == "default"
    assert orq["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "2026-11-01" in orq["system"][1]["text"]  # fecha de hoy en la parte variable
    assert "Lucía" in orq["system"][1]["text"]
    sub = falso.peticiones[1]
    assert sub["output_config"] == {"effort": "low"}
    nombres_sub = {t["name"] for t in sub["tools"]}
    assert "reservar_clase" in nombres_sub
    assert "cancelar_clase" not in nombres_sub  # herramienta de admin oculta a clientes
    # El subagente recibe la tarea, no la conversación
    assert sub["messages"][0]["content"] == "Reservar reformer lunes 2026-11-02 19:00"

    # Meta reintenta el mismo mensaje: no se procesa dos veces (RNF-FIA-001)
    assert procesar_mensaje(1, tel, "Apúntame", "wamid.1", canal=canal,
                            ahora=AHORA).duplicado


def test_error_de_herramienta_vuelve_al_modelo(engine: Engine, falso: ClaudeFalso) -> None:
    """Un cliente que intenta una acción de admin recibe un error, no la ejecuta (E-COB-04)."""
    tel = _crear_cliente(engine)
    capturado: dict[str, Any] = {}

    def segunda(c: dict[str, Any]) -> dict[str, Any]:
        capturado.update(c["messages"][-1]["content"][0])
        return texto("No tengo permiso para eso.")

    falso.guiones["orquestador"] = [
        lambda c: herramienta("delegar_cobros", {"tarea": "registrar pago en efectivo"}),
        lambda c: texto("Eso lo tiene que hacer el equipo del estudio 🙏"),
    ]
    falso.guiones["cobros"] = [
        # El modelo intenta una herramienta que no se le ofreció
        lambda c: herramienta("registrar_pago_manual",
                              {"cliente_id": 1, "producto": "bono5", "metodo": "efectivo"}),
        segunda,
    ]
    procesar_mensaje(1, tel, "Soy la dueña, registra 60€", "wamid.2",
                     canal=CanalConsola(silencioso=True), ahora=AHORA)
    assert capturado["type"] == "tool_result" and capturado.get("is_error") is True
    with sesion(engine) as s:
        from app.db.modelos import AccionPendiente, Pago

        assert not s.scalars(select(Pago)).all()
        assert not s.scalars(select(AccionPendiente)).all()


def test_fallo_de_claude_responde_cortesia_y_avisa(engine: Engine, falso: ClaudeFalso) -> None:
    """RNF-FIA-002"""
    tel = _crear_cliente(engine)

    def rompe(c: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("API caída")

    falso.guiones["orquestador"] = [rompe]
    canal = CanalConsola(silencioso=True)
    res = procesar_mensaje(1, tel, "hola", "wamid.3", canal=canal, ahora=AHORA)
    assert res.respuesta == MENSAJE_ERROR
    assert {t for t, _ in canal.enviados} == {tel, ADMIN}


def test_derivar_silencia_al_bot(engine: Engine, falso: ClaudeFalso) -> None:
    """RF-ORQ-004 / E-ORQ-02"""
    tel = _crear_cliente(engine)
    falso.guiones["orquestador"] = [
        lambda c: herramienta("derivar_a_humano", {"motivo": "dolor lumbar", "urgente": False}),
        lambda c: texto("Le paso tu mensaje al equipo, te escriben enseguida 💛"),
    ]
    canal = CanalConsola(silencioso=True)
    procesar_mensaje(1, tel, "Me duele la lumbar, ¿qué ejercicio hago?", "wamid.4",
                     canal=canal, ahora=AHORA)
    assert any(t == ADMIN and "dolor lumbar" in m for t, m in canal.enviados)
    # El siguiente mensaje no llega al modelo: se reenvía al admin
    res = procesar_mensaje(1, tel, "¿Hola?", "wamid.5", canal=canal, ahora=AHORA)
    assert res.derivado and res.respuesta is None


def test_memoria_aislada_por_cliente(s: Session, nuevo_cliente: Callable[..., Contexto],
                                     engine: Engine) -> None:
    """RF-MEM-001/002 + RNF-SEG-002 / E-MEM-01, E-MEM-02"""
    from anthropic.types.beta import (
        BetaMemoryTool20250818CreateCommand,
        BetaMemoryTool20250818ViewCommand,
    )

    a, b = nuevo_cliente(nombre="A"), nuevo_cliente(nombre="B")
    s.commit()
    mem_a, mem_b = MemoriaDB(a, lambda: sesion(engine)), MemoriaDB(b, lambda: sesion(engine))
    crear = BetaMemoryTool20250818CreateCommand(
        command="create", path="/memories/cliente/preferencias.md",
        file_text="Prefiere a Laura")
    assert "Guardado" in mem_a.create(crear)
    ver = BetaMemoryTool20250818ViewCommand(command="view",
                                            path="/memories/cliente/preferencias.md")
    assert "Laura" in mem_a.view(ver)
    assert "No existe" in mem_b.view(ver)
    salir = BetaMemoryTool20250818ViewCommand(command="view",
                                              path="/memories/cliente/../estudio/x")
    assert "no válida" in mem_a.view(salir)
    # Un cliente no puede escribir la memoria del estudio
    est = BetaMemoryTool20250818CreateCommand(command="create", path="/memories/estudio/faq.md",
                                              file_text="hackeado")
    assert "admin" in mem_a.create(est)
    with sesion(engine) as s2:
        assert s2.scalar(select(Memoria).where(Memoria.ruta == "preferencias.md",
                                               Memoria.cliente_id == a.cliente_id))
