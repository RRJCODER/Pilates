"""Simulador de WhatsApp en el navegador (RF-PAN-012). Solo con CANAL=consola.

Sirve para probar en local, sin Meta, lo que recibe cada número: los menús con
sus botones, los avisos del estudio y el código de acceso al panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.integraciones.canal import MAX_BOTONES, MAX_FILAS, MAX_TITULO_BOTON, Fila, recortar


@dataclass
class Burbuja:
    de_mi: bool  # True: lo escribe la persona; False: lo envía el estudio
    texto: str
    opciones: list[tuple[str, str]] = field(default_factory=list)
    hora: str = field(default_factory=lambda: datetime.now(UTC).strftime("%H:%M"))


@dataclass
class CanalWeb:
    """Canal que guarda en memoria lo enviado a cada número, para pintarlo como un chat."""

    chats: dict[str, list[Burbuja]] = field(default_factory=dict)

    def _add(self, telefono: str, b: Burbuja) -> None:
        self.chats.setdefault(telefono, []).append(b)
        print(f"\n📤 → {telefono}\n{b.texto}\n", flush=True)

    def entrante(self, telefono: str, texto: str) -> None:
        self.chats.setdefault(telefono, []).append(Burbuja(True, texto))

    def enviar_texto(self, telefono: str, texto: str) -> None:
        self._add(telefono, Burbuja(False, texto))

    def enviar_plantilla(self, telefono: str, plantilla: str, parametros: list[str],
                         idioma: str = "es") -> None:
        if plantilla == "codigo_acceso":
            texto = (f"🔐 *{parametros[0]}* es tu código para entrar en el panel del estudio. "
                     "Caduca en 10 minutos. No lo compartas.")
        else:
            texto = f"[plantilla {plantilla}] " + " | ".join(parametros)
        self._add(telefono, Burbuja(False, texto))

    def enviar_botones(self, telefono: str, texto: str,
                       botones: list[tuple[str, str]]) -> None:
        self._add(telefono, Burbuja(False, texto, [
            (i, recortar(t, MAX_TITULO_BOTON)) for i, t in botones[:MAX_BOTONES]]))

    def enviar_lista(self, telefono: str, texto: str, etiqueta: str,
                     filas: list[Fila]) -> None:
        opciones = [(f.id, f.titulo + (f" · {f.descripcion}" if f.descripcion else ""))
                    for f in filas[:MAX_FILAS]]
        self._add(telefono, Burbuja(False, texto, opciones))
