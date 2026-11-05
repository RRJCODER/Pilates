"""Canales de mensajería (RF-GEN-004, diseño §8).

- `CanalConsola`: para desarrollo; imprime los mensajes (y los guarda para tests).
- `CanalWhatsApp`: Meta WhatsApp Cloud API. Fuera de la ventana de 24 h solo se
  pueden enviar plantillas aprobadas.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from config.settings import Settings, get_settings

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com/v21.0"


class Canal(Protocol):
    def enviar_texto(self, telefono: str, texto: str) -> None: ...

    def enviar_plantilla(self, telefono: str, plantilla: str, parametros: list[str],
                         idioma: str = "es") -> None: ...


@dataclass
class CanalConsola:
    enviados: list[tuple[str, str]] = field(default_factory=list)
    silencioso: bool = False

    def enviar_texto(self, telefono: str, texto: str) -> None:
        self.enviados.append((telefono, texto))
        if not self.silencioso:
            print(f"\n📤 → {telefono}\n{texto}\n")

    def enviar_plantilla(self, telefono: str, plantilla: str, parametros: list[str],
                         idioma: str = "es") -> None:
        self.enviar_texto(telefono, f"[plantilla {plantilla}] " + " | ".join(parametros))


class CanalWhatsApp:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self._http = httpx.Client(base_url=GRAPH, timeout=20,
                                  headers={"Authorization": f"Bearer {cfg.whatsapp_token}"})

    def _enviar(self, cuerpo: dict[str, object]) -> None:
        r = self._http.post(f"/{self.cfg.whatsapp_phone_number_id}/messages",
                            json={"messaging_product": "whatsapp", **cuerpo})
        if r.status_code >= 400:
            log.error("WhatsApp %s: %s", r.status_code, r.text)
            r.raise_for_status()

    def enviar_texto(self, telefono: str, texto: str) -> None:
        # WhatsApp admite 4096 caracteres por mensaje
        for i in range(0, len(texto), 4000):
            self._enviar({"to": telefono.lstrip("+"), "type": "text",
                          "text": {"preview_url": True, "body": texto[i:i + 4000]}})

    def enviar_plantilla(self, telefono: str, plantilla: str, parametros: list[str],
                         idioma: str = "es") -> None:
        componentes = [{"type": "body", "parameters": [
            {"type": "text", "text": p[:1000]} for p in parametros]}] if parametros else []
        self._enviar({"to": telefono.lstrip("+"), "type": "template",
                      "template": {"name": plantilla, "language": {"code": idioma},
                                   "components": componentes}})

    def descargar_medio(self, media_id: str) -> tuple[bytes, str]:
        meta = self._http.get(f"/{media_id}")
        meta.raise_for_status()
        info = meta.json()
        r = self._http.get(info["url"])
        r.raise_for_status()
        return r.content, info.get("mime_type", "application/octet-stream")


def firma_valida(cuerpo: bytes, cabecera: str | None, secreto: str) -> bool:
    """RNF-SEG-001: X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app_secret, cuerpo)."""
    if not cabecera or not cabecera.startswith("sha256=") or not secreto:
        return False
    esperado = hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecera.removeprefix("sha256="))


_canal: Canal | None = None


def get_canal() -> Canal:
    global _canal
    if _canal is None:
        cfg = get_settings()
        _canal = CanalWhatsApp(cfg) if cfg.canal == "whatsapp" else CanalConsola()
    return _canal


def set_canal(canal: Canal | None) -> None:
    global _canal
    _canal = canal
