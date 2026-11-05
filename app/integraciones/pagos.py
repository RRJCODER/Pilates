"""Adaptadores de pago (RF-GEN-002, RF-COB-001)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from config.settings import Settings, get_settings


@dataclass
class EnlacePago:
    url: str
    referencia: str


class PagosAdapter(Protocol):
    nombre: str

    def crear_enlace(self, *, pago_id: int, concepto: str, importe: Decimal,
                     telefono: str, email: str | None) -> EnlacePago: ...

    def devolver(self, referencia: str, importe: Decimal) -> None: ...


class PagosFake:
    """Para desarrollo y evals: el pago se simula con `POST /dev/pagar/{ref}`
    o con `:pagar <ref>` en la consola."""

    nombre = "fake"

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.devoluciones: list[str] = []

    def crear_enlace(self, *, pago_id: int, concepto: str, importe: Decimal,
                     telefono: str, email: str | None) -> EnlacePago:
        ref = f"fake_{pago_id}_{secrets.token_hex(4)}"
        return EnlacePago(f"{self.base_url}/dev/pagar/{ref}", ref)

    def devolver(self, referencia: str, importe: Decimal) -> None:
        self.devoluciones.append(referencia)


class PagosStripe:
    """Stripe Checkout. La confirmación llega por `POST /webhook/stripe`
    (evento `checkout.session.completed`); la referencia es el id de la sesión."""

    nombre = "stripe"

    def __init__(self, cfg: Settings) -> None:
        import stripe

        self._stripe = stripe
        self._cliente = stripe.StripeClient(cfg.stripe_api_key)
        self.base_url = cfg.base_url_publica.rstrip("/")

    def crear_enlace(self, *, pago_id: int, concepto: str, importe: Decimal,
                     telefono: str, email: str | None) -> EnlacePago:
        params: Any = {
            "mode": "payment",
            "line_items": [{"quantity": 1, "price_data": {
                "currency": "eur", "unit_amount": int(importe * 100),
                "product_data": {"name": concepto}}}],
            "customer_email": email or None,
            "client_reference_id": str(pago_id),
            "metadata": {"pago_id": str(pago_id), "telefono": telefono},
            "payment_intent_data": {"metadata": {"pago_id": str(pago_id)}},
            "success_url": f"{self.base_url}/pago/ok",
            "cancel_url": f"{self.base_url}/pago/cancelado",
        }
        if not email:
            del params["customer_email"]
        sesion = self._cliente.v1.checkout.sessions.create(params=params)
        assert sesion.url is not None
        return EnlacePago(sesion.url, sesion.id)

    def devolver(self, referencia: str, importe: Decimal) -> None:
        sesion = self._cliente.v1.checkout.sessions.retrieve(referencia)
        pi = sesion.payment_intent
        pi_id = pi if isinstance(pi, str) else pi.id if pi else None
        if pi_id is None:
            raise RuntimeError("La sesión de Stripe no tiene payment_intent")
        self._cliente.v1.refunds.create(params={"payment_intent": pi_id,
                                                "amount": int(importe * 100)})


_instancia: PagosAdapter | None = None


def get_pagos() -> PagosAdapter:
    global _instancia
    if _instancia is None:
        cfg = get_settings()
        _instancia = PagosStripe(cfg) if cfg.pagos_proveedor == "stripe" else PagosFake(
            cfg.base_url_publica)
    return _instancia


def set_pagos(adapter: PagosAdapter | None) -> None:
    """Para tests."""
    global _instancia
    _instancia = adapter
