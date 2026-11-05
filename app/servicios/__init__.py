"""Lógica de negocio pura, testeable sin LLM (constitución §4.3).

Se importan todos los módulos para registrar los ejecutores de acciones pendientes.
"""

from app.servicios import (  # noqa: F401
    acciones,
    bonos,
    clases,
    clientes,
    conversaciones,
    lista_espera,
    marketing,
    pagos,
    reservas,
)
