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


# Límites de los mensajes interactivos de WhatsApp
MAX_BOTONES, MAX_TITULO_BOTON = 3, 20
MAX_FILAS, MAX_TITULO_FILA, MAX_DESC_FILA, MAX_ETIQUETA_LISTA = 10, 24, 72, 20


def recortar(texto: str, n: int) -> str:
    return texto if len(texto) <= n else texto[: n - 1] + "…"


@dataclass
class Fila:
    id: str
    titulo: str
    descripcion: str = ""


class Canal(Protocol):
    def enviar_texto(self, telefono: str, texto: str) -> None: ...

    def enviar_plantilla(self, telefono: str, plantilla: str, parametros: list[str],
                         idioma: str = "es") -> None: ...

    def enviar_botones(self, telefono: str, texto: str,
                       botones: list[tuple[str, str]]) -> None:
        """Hasta 3 botones de respuesta: (id, título)."""
        ...

    def enviar_lista(self, telefono: str, texto: str, etiqueta: str,
                     filas: list[Fila]) -> None:
        """Lista desplegable de hasta 10 opciones."""
        ...


@dataclass
class CanalConsola:
    enviados: list[tuple[str, str]] = field(default_factory=list)
    silencioso: bool = False
    # Últimas opciones mostradas a cada teléfono, para elegirlas con "#n" en la consola
    opciones: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def enviar_botones(self, telefono: str, texto: str,
                       botones: list[tuple[str, str]]) -> None:
        botones = [(i, recortar(tit, MAX_TITULO_BOTON)) for i, tit in botones[:MAX_BOTONES]]
        self.opciones[telefono] = botones
        lineas = "\n".join(f"  [{n}] {tit}" for n, (_, tit) in enumerate(botones, 1))
        self.enviar_texto(telefono, f"{texto}\n{lineas}")

    def enviar_lista(self, telefono: str, texto: str, etiqueta: str,
                     filas: list[Fila]) -> None:
        filas = filas[:MAX_FILAS]
        self.opciones[telefono] = [(f.id, recortar(f.titulo, MAX_TITULO_FILA)) for f in filas]
        lineas = "\n".join(
            f"  [{n}] {recortar(f.titulo, MAX_TITULO_FILA)}"
            + (f" · {recortar(f.descripcion, MAX_DESC_FILA)}" if f.descripcion else "")
            for n, f in enumerate(filas, 1))
        self.enviar_texto(telefono, f"{texto}\n({etiqueta})\n{lineas}")

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

    def enviar_botones(self, telefono: str, texto: str,
                       botones: list[tuple[str, str]]) -> None:
        self._enviar({"to": telefono.lstrip("+"), "type": "interactive", "interactive": {
            "type": "button", "body": {"text": recortar(texto, 1024)},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": i, "title": recortar(tit, MAX_TITULO_BOTON)}}
                for i, tit in botones[:MAX_BOTONES]]}}})

    def enviar_lista(self, telefono: str, texto: str, etiqueta: str,
                     filas: list[Fila]) -> None:
        self._enviar({"to": telefono.lstrip("+"), "type": "interactive", "interactive": {
            "type": "list", "body": {"text": recortar(texto, 1024)},
            "action": {"button": recortar(etiqueta, MAX_ETIQUETA_LISTA), "sections": [{
                "title": recortar(etiqueta, MAX_TITULO_FILA),
                "rows": [{"id": f.id, "title": recortar(f.titulo, MAX_TITULO_FILA),
                          **({"description": recortar(f.descripcion, MAX_DESC_FILA)}
                             if f.descripcion else {})}
                         for f in filas[:MAX_FILAS]]}]}}})

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
