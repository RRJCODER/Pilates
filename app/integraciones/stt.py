"""Transcripción de notas de voz (RF-ORQ-006, RF-GEN-002).

El proveedor está por decidir (diseño §8). Mientras tanto, `STTFake` no transcribe:
devuelve None y el asistente pide amablemente que lo escriban. Para añadir un
proveedor, implementa `transcribir` y regístralo en `get_stt`.
"""

from __future__ import annotations

from typing import Protocol


class STTAdapter(Protocol):
    def transcribir(self, audio: bytes, mime: str) -> str | None: ...


class STTFake:
    def __init__(self, texto: str | None = None) -> None:
        self.texto = texto

    def transcribir(self, audio: bytes, mime: str) -> str | None:
        return self.texto


_instancia: STTAdapter | None = None


def get_stt() -> STTAdapter:
    global _instancia
    if _instancia is None:
        _instancia = STTFake()
    return _instancia


def set_stt(adapter: STTAdapter | None) -> None:
    global _instancia
    _instancia = adapter
