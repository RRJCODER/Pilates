"""Adaptadores de facturación (RF-GEN-002, RF-COB-004, constitución §3.4).

El agente no emite facturas legales: las delega en un software homologado (Verifactu).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import httpx

from config.settings import get_settings


@dataclass
class DatosFactura:
    pago_id: int
    nombre: str
    email: str | None
    telefono: str
    concepto: str
    importe: Decimal  # IVA incluido


class FacturacionAdapter(Protocol):
    nombre: str

    def emitir(self, datos: DatosFactura) -> str | None:
        """Devuelve la referencia de la factura, o None si no hay facturación."""
        ...

    def url_pdf(self, referencia: str) -> str | None: ...


class FacturacionNinguna:
    nombre = "ninguno"

    def emitir(self, datos: DatosFactura) -> str | None:
        return None

    def url_pdf(self, referencia: str) -> str | None:
        return None


class FacturacionHolded:
    """Holded API v1 (https://developers.holded.com). Crea la factura como documento
    `invoice` y la envía por email al cliente desde Holded."""

    nombre = "holded"
    BASE = "https://api.holded.com/api/invoicing/v1"

    def __init__(self, api_key: str, iva: float = 21.0) -> None:
        self._http = httpx.Client(base_url=self.BASE, headers={"key": api_key}, timeout=20)
        self.iva = iva

    def emitir(self, datos: DatosFactura) -> str | None:
        base_imponible = float(datos.importe) / (1 + self.iva / 100)
        r = self._http.post("/documents/invoice", json={
            "contactName": datos.nombre,
            "contactEmail": datos.email,
            "desc": datos.concepto,
            "items": [{"name": datos.concepto, "units": 1,
                       "subtotal": round(base_imponible, 2), "tax": self.iva}],
            "customFields": [{"field": "pago_id", "value": str(datos.pago_id)}],
        })
        r.raise_for_status()
        ref: str = r.json()["id"]
        if datos.email:
            self._http.post(f"/documents/invoice/{ref}/send", json={"emails": datos.email})
        return ref

    def url_pdf(self, referencia: str) -> str | None:
        # Holded no da una URL pública del PDF; la factura llega por email.
        return None


_instancia: FacturacionAdapter | None = None


def get_facturacion() -> FacturacionAdapter:
    global _instancia
    if _instancia is None:
        cfg = get_settings()
        _instancia = (FacturacionHolded(cfg.holded_api_key)
                      if cfg.facturacion_proveedor == "holded" else FacturacionNinguna())
    return _instancia


def set_facturacion(adapter: FacturacionAdapter | None) -> None:
    global _instancia
    _instancia = adapter
