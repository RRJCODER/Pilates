"""Modelo de datos (specs/02-diseno.md §9).

Todas las tablas de negocio llevan `estudio_id` (RF-GEN-003).
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, datetime: DateTime(timezone=True)}


# --------------------------------------------------------------------- enums


class RolStaff(enum.StrEnum):
    ADMIN = "admin"
    INSTRUCTOR = "instructor"


class EstadoCliente(enum.StrEnum):
    LEAD = "lead"
    ACTIVO = "activo"
    INACTIVO = "inactivo"
    BORRADO = "borrado"


class Modalidad(enum.StrEnum):
    REFORMER = "reformer"
    SUELO = "suelo"


class EstadoClase(enum.StrEnum):
    PROGRAMADA = "programada"
    CANCELADA = "cancelada"


class EstadoReserva(enum.StrEnum):
    CONFIRMADA = "confirmada"
    CANCELADA = "cancelada"
    ASISTIO = "asistio"
    FALTA = "falta"


class EstadoEspera(enum.StrEnum):
    ESPERANDO = "esperando"
    OFRECIDA = "ofrecida"
    ACEPTADA = "aceptada"
    EXPIRADA = "expirada"
    RETIRADA = "retirada"


class TipoProducto(enum.StrEnum):
    BONO = "bono"
    MENSUALIDAD = "mensualidad"
    SUELTA = "suelta"


class EstadoBono(enum.StrEnum):
    PENDIENTE_PAGO = "pendiente_pago"
    ACTIVO = "activo"
    CONGELADO = "congelado"
    CADUCADO = "caducado"
    AGOTADO = "agotado"
    ANULADO = "anulado"


class EstadoPago(enum.StrEnum):
    PENDIENTE = "pendiente"
    PAGADO = "pagado"
    DEVUELTO = "devuelto"
    FALLIDO = "fallido"


class EstadoAccion(enum.StrEnum):
    PENDIENTE = "pendiente"
    EJECUTADA = "ejecutada"
    EXPIRADA = "expirada"
    RECHAZADA = "rechazada"


class AmbitoMemoria(enum.StrEnum):
    CLIENTE = "cliente"
    ESTUDIO = "estudio"


class EstadoConversacion(enum.StrEnum):
    BOT = "bot"
    HUMANO = "humano"


class EstadoCampana(enum.StrEnum):
    BORRADOR = "borrador"
    APROBADA = "aprobada"
    ENVIADA = "enviada"
    DESCARTADA = "descartada"


def _str(n: int = 20) -> Any:
    return mapped_column(String(n))


# --------------------------------------------------------------------- tablas


class Estudio(Base):
    __tablename__ = "estudio"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    zona_horaria: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Staff(Base):
    __tablename__ = "staff"
    __table_args__ = (UniqueConstraint("estudio_id", "telefono"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    telefono: Mapped[str] = mapped_column(String(20))
    rol: Mapped[RolStaff] = _str()
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Cliente(Base):
    __tablename__ = "clientes"
    __table_args__ = (UniqueConstraint("estudio_id", "telefono"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    telefono: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(200))
    nivel: Mapped[str | None] = mapped_column(String(20))
    objetivos: Mapped[str | None] = mapped_column(Text)
    fecha_nac: Mapped[date | None] = mapped_column(Date)
    como_nos_conocio: Mapped[str | None] = mapped_column(String(120))
    consent_salud: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_marketing: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_fecha: Mapped[datetime | None]
    estado: Mapped[EstadoCliente] = mapped_column(String(20), default=EstadoCliente.ACTIVO)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())

    bonos: Mapped[list[Bono]] = relationship(back_populates="cliente")


class Lesion(Base):
    __tablename__ = "lesiones"
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    descripcion: Mapped[str] = mapped_column(Text)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class TipoClase(Base):
    __tablename__ = "tipos_clase"
    __table_args__ = (UniqueConstraint("estudio_id", "clave"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    clave: Mapped[str] = mapped_column(String(60))
    nombre: Mapped[str] = mapped_column(String(120))
    modalidad: Mapped[Modalidad] = _str()
    nivel: Mapped[str] = mapped_column(String(20))
    duracion_min: Mapped[int] = mapped_column(Integer)
    aforo: Mapped[int] = mapped_column(Integer)


class Clase(Base):
    __tablename__ = "clases"
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    tipo_id: Mapped[int] = mapped_column(ForeignKey("tipos_clase.id"))
    instructor_id: Mapped[int] = mapped_column(ForeignKey("staff.id"))
    inicio: Mapped[datetime] = mapped_column(index=True)
    fin: Mapped[datetime]
    aforo: Mapped[int] = mapped_column(Integer)
    estado: Mapped[EstadoClase] = mapped_column(String(20), default=EstadoClase.PROGRAMADA)

    tipo: Mapped[TipoClase] = relationship()
    instructor: Mapped[Staff] = relationship()


class Reserva(Base):
    __tablename__ = "reservas"
    __table_args__ = (UniqueConstraint("clase_id", "cliente_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    clase_id: Mapped[int] = mapped_column(ForeignKey("clases.id"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    bono_id: Mapped[int | None] = mapped_column(ForeignKey("bonos.id"))
    estado: Mapped[EstadoReserva] = mapped_column(String(20), default=EstadoReserva.CONFIRMADA)
    creada_en: Mapped[datetime] = mapped_column(server_default=func.now())
    cancelada_en: Mapped[datetime | None]

    clase: Mapped[Clase] = relationship()


class ListaEspera(Base):
    __tablename__ = "lista_espera"
    __table_args__ = (UniqueConstraint("clase_id", "cliente_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    clase_id: Mapped[int] = mapped_column(ForeignKey("clases.id"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"))
    posicion: Mapped[int] = mapped_column(Integer)
    ofrecida_en: Mapped[datetime | None]
    expira_en: Mapped[datetime | None]
    estado: Mapped[EstadoEspera] = mapped_column(String(20), default=EstadoEspera.ESPERANDO)


class Producto(Base):
    __tablename__ = "productos"
    __table_args__ = (UniqueConstraint("estudio_id", "clave"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    clave: Mapped[str] = mapped_column(String(60))
    nombre: Mapped[str] = mapped_column(String(120))
    tipo: Mapped[TipoProducto] = _str()
    sesiones: Mapped[int] = mapped_column(Integer)
    validez_dias: Mapped[int] = mapped_column(Integer)
    precio: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Bono(Base):
    __tablename__ = "bonos"
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos.id"))
    sesiones_total: Mapped[int] = mapped_column(Integer)
    sesiones_usadas: Mapped[int] = mapped_column(Integer, default=0)
    inicio: Mapped[date | None] = mapped_column(Date)
    caduca: Mapped[date | None] = mapped_column(Date)
    congelado_hasta: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[EstadoBono] = mapped_column(String(20), default=EstadoBono.PENDIENTE_PAGO)
    pago_id: Mapped[int | None] = mapped_column(ForeignKey("pagos.id"))

    cliente: Mapped[Cliente] = relationship(back_populates="bonos")
    producto: Mapped[Producto] = relationship()

    @property
    def sesiones_restantes(self) -> int:
        return self.sesiones_total - self.sesiones_usadas


class Pago(Base):
    __tablename__ = "pagos"
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    producto_id: Mapped[int | None] = mapped_column(ForeignKey("productos.id"))
    importe: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    metodo: Mapped[str] = mapped_column(String(30))
    estado: Mapped[EstadoPago] = mapped_column(String(20), default=EstadoPago.PENDIENTE)
    proveedor_ref: Mapped[str | None] = mapped_column(String(200), index=True)
    enlace_pago: Mapped[str | None] = mapped_column(String(500))
    factura_ref: Mapped[str | None] = mapped_column(String(200))
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())
    pagado_en: Mapped[datetime | None]


class AccionPendiente(Base):
    """Acciones irreversibles a la espera de confirmación (diseño §7)."""

    __tablename__ = "acciones_pendientes"
    codigo: Mapped[str] = mapped_column(String(8), primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(60))
    descripcion: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    quien_confirma: Mapped[str] = mapped_column(String(20))  # teléfono
    expira_en: Mapped[datetime]
    estado: Mapped[EstadoAccion] = mapped_column(String(20), default=EstadoAccion.PENDIENTE)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class Memoria(Base):
    """Memoria a largo plazo, expuesta como /memories/ (diseño §6.1)."""

    __tablename__ = "memorias"
    __table_args__ = (UniqueConstraint("estudio_id", "ambito", "cliente_id", "ruta"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    ambito: Mapped[AmbitoMemoria] = _str()
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id"), index=True)
    ruta: Mapped[str] = mapped_column(String(300))
    contenido: Mapped[str] = mapped_column(Text, default="")
    actualizado_en: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Conversacion(Base):
    __tablename__ = "conversaciones"
    __table_args__ = (UniqueConstraint("estudio_id", "telefono"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    telefono: Mapped[str] = mapped_column(String(20))
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id"))
    estado: Mapped[EstadoConversacion] = mapped_column(String(20), default=EstadoConversacion.BOT)
    ultima_entrada_en: Mapped[datetime | None]
    humano_hasta: Mapped[datetime | None]


class Mensaje(Base):
    __tablename__ = "mensajes"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversacion_id: Mapped[int] = mapped_column(ForeignKey("conversaciones.id"), index=True)
    wa_message_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    rol: Mapped[str] = mapped_column(String(20))  # user | assistant
    contenido: Mapped[str] = mapped_column(Text)
    resumido: Mapped[bool] = mapped_column(Boolean, default=False)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class Resumen(Base):
    __tablename__ = "resumenes"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversacion_id: Mapped[int] = mapped_column(ForeignKey("conversaciones.id"), index=True)
    contenido: Mapped[str] = mapped_column(Text)
    hasta_mensaje_id: Mapped[int] = mapped_column(Integer)


class Campana(Base):
    __tablename__ = "campanas"
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    segmento: Mapped[dict[str, Any]] = mapped_column(default=dict)
    texto: Mapped[str] = mapped_column(Text)
    plantilla: Mapped[str | None] = mapped_column(String(100))
    estado: Mapped[EstadoCampana] = mapped_column(String(20), default=EstadoCampana.BORRADOR)
    aprobada_por: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    enviada_en: Mapped[datetime | None]


class EventoAgente(Base):
    """Auditoría de cada llamada de agente y herramienta (RNF-OBS-001)."""

    __tablename__ = "eventos_agente"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversacion_id: Mapped[int | None] = mapped_column(ForeignKey("conversaciones.id"))
    agente: Mapped[str] = mapped_column(String(40))
    herramienta: Mapped[str | None] = mapped_column(String(80))
    entrada: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    salida: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latencia_ms: Mapped[int | None] = mapped_column(Integer)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class NotaInterna(Base):
    """Notas del staff sobre un cliente; el cliente nunca las ve (RF-CLI-006)."""

    __tablename__ = "notas_internas"
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    texto: Mapped[str] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class EnvioProgramado(Base):
    """Registro de envíos del cron para no repetirlos (idempotencia)."""

    __tablename__ = "envios_programados"
    __table_args__ = (UniqueConstraint("estudio_id", "clave"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(ForeignKey("estudio.id"), index=True)
    clave: Mapped[str] = mapped_column(String(200))
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())
