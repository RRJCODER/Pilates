"""Acceso a Claude: cliente, ejecución de un agente con Tool Runner y auditoría.

Diseño §3: modelo y effort configurables, thinking adaptativo (por defecto en
Opus 5.5), fallback de servidor ante rechazos y prompt caching del bloque estable.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic.types.beta import BetaMessage

from app.db.modelos import EventoAgente
from app.db.sesion import sesion
from app.servicios.base import Contexto
from config.settings import get_settings

log = logging.getLogger(__name__)

BETA_FALLBACK = "server-side-fallback-2026-07-01"
MAX_TOKENS = 4000          # respuestas de WhatsApp: cortas
MAX_ITERACIONES = 12       # tope de idas y vueltas con herramientas por agente

_cliente: anthropic.Anthropic | None = None


def get_cliente() -> anthropic.Anthropic:
    global _cliente
    if _cliente is None:
        _cliente = anthropic.Anthropic(api_key=get_settings().anthropic_api_key or None)
    return _cliente


def set_cliente(cliente: anthropic.Anthropic | None) -> None:
    """Para tests (cliente con transporte simulado)."""
    global _cliente
    _cliente = cliente


class Rechazo(Exception):
    """Claude rechazó la petición incluso tras el fallback (stop_reason == 'refusal')."""


@dataclass
class Uso:
    tokens_in: int = 0
    tokens_out: int = 0
    cache_leidos: int = 0
    llamadas: int = 0
    por_agente: dict[str, int] = field(default_factory=dict)

    def sumar(self, agente: str, msg: BetaMessage) -> None:
        u = msg.usage
        self.tokens_in += u.input_tokens + (u.cache_creation_input_tokens or 0)
        self.cache_leidos += u.cache_read_input_tokens or 0
        self.tokens_out += u.output_tokens
        self.llamadas += 1
        self.por_agente[agente] = self.por_agente.get(agente, 0) + 1


def system_prompt(estable: str, variable: str) -> list[dict[str, Any]]:
    """Bloque estable cacheado + bloque variable después del punto de caché."""
    return [
        {"type": "text", "text": estable, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": variable},
    ]


def texto_final(msg: BetaMessage) -> str:
    return "\n".join(b.text for b in msg.content if b.type == "text").strip()


def ejecutar_agente(*, agente: str, ctx: Contexto, modelo: str, effort: str,
                    system: list[dict[str, Any]], tools: Sequence[Any],
                    mensajes: list[dict[str, Any]], uso: Uso | None = None) -> str:
    """Ejecuta un agente hasta que deja de llamar herramientas y devuelve su texto final."""
    cliente = get_cliente()
    inicio = time.monotonic()
    params: dict[str, Any] = {
        "model": modelo,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "tools": list(tools),
        "messages": mensajes,
        "output_config": {"effort": effort},
        "fallbacks": "default",
        "betas": [BETA_FALLBACK],
        "max_iterations": MAX_ITERACIONES,
    }
    runner = cliente.beta.messages.tool_runner(**params)
    ultimo: BetaMessage | None = None
    for msg in runner:
        ultimo = msg
        if uso is not None:
            uso.sumar(agente, msg)
    if ultimo is None:
        raise RuntimeError(f"El agente {agente} no devolvió ningún mensaje")
    if ultimo.stop_reason == "refusal":
        raise Rechazo(f"{agente}: {getattr(ultimo, 'stop_details', None)}")
    texto = texto_final(ultimo)
    registrar_evento(ctx, agente, None, {"mensajes": len(mensajes)},
                     {"texto": texto[:2000], "stop_reason": ultimo.stop_reason},
                     ultimo, int((time.monotonic() - inicio) * 1000))
    return texto


def registrar_evento(ctx: Contexto, agente: str, herramienta: str | None,
                     entrada: dict[str, Any] | None, salida: Any,
                     msg: BetaMessage | None = None, latencia_ms: int | None = None) -> None:
    """RNF-OBS-001. Nunca debe romper el turno."""
    try:
        with sesion() as s:
            s.add(EventoAgente(
                conversacion_id=ctx.conversacion_id, agente=agente, herramienta=herramienta,
                entrada=_jsonable(entrada), salida=_jsonable(salida),
                tokens_in=msg.usage.input_tokens if msg else None,
                tokens_out=msg.usage.output_tokens if msg else None,
                latencia_ms=latencia_ms))
    except Exception:  # pragma: no cover - la auditoría no puede tumbar la conversación
        log.exception("No se pudo registrar el evento del agente")


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, dict):
        return json.loads(json.dumps(v, default=str, ensure_ascii=False))
    return {"valor": json.loads(json.dumps(v, default=str, ensure_ascii=False))}


def config_modelo(rol: str) -> tuple[str, str]:
    cfg = get_settings()
    if rol == "orquestador":
        return cfg.modelo_orquestador, cfg.effort_orquestador
    return cfg.modelo_subagentes, cfg.effort_subagentes


Fabrica = Callable[[Contexto], list[Any]]
