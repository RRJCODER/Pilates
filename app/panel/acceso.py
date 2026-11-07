"""Acceso al panel: código por WhatsApp, sesiones y CSRF (RF-PAN-001, RNF-SEG-004).

- El código (6 cifras) y el token de sesión solo se guardan con hash.
- Como mucho 5 intentos por código y 3 códigos por teléfono cada 15 minutos.
- Si el teléfono no es de staff, no se envía nada y la respuesta es la misma.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.modelos import CodigoAcceso, SesionPanel, Staff
from app.integraciones.canal import Canal, get_canal
from app.servicios.base import a_utc
from config.settings import get_settings

MAX_INTENTOS = 5
MAX_CODIGOS = 3
VENTANA_CODIGOS = timedelta(minutes=15)


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def normalizar_telefono(tel: str) -> str:
    digitos = "".join(ch for ch in tel if ch.isdigit())
    if len(digitos) == 9:  # número español sin prefijo
        digitos = "34" + digitos
    return "+" + digitos


def _staff(s: Session, estudio_id: int, telefono: str) -> Staff | None:
    return s.scalar(select(Staff).where(Staff.estudio_id == estudio_id,
                                        Staff.telefono == telefono, Staff.activo.is_(True)))


def pedir_codigo(s: Session, estudio_id: int, telefono: str, ahora: datetime,
                 canal: Canal | None = None) -> bool:
    """Envía un código si el teléfono es de staff. Devuelve si se ha enviado (solo para logs)."""
    staff = _staff(s, estudio_id, telefono)
    if staff is None:
        return False
    recientes = s.scalar(select(func.count()).select_from(CodigoAcceso).where(
        CodigoAcceso.staff_id == staff.id,
        CodigoAcceso.creado_en >= ahora - VENTANA_CODIGOS)) or 0
    if recientes >= MAX_CODIGOS:
        return False
    codigo = f"{secrets.randbelow(10**6):06d}"
    s.add(CodigoAcceso(staff_id=staff.id, codigo_hash=_hash(f"{staff.id}:{codigo}"),
                       expira_en=ahora + timedelta(minutes=get_settings().panel_codigo_minutos),
                       creado_en=ahora))
    s.flush()
    # Plantilla de autenticación: se puede enviar aunque la ventana de 24 h esté cerrada.
    # No pasa por `entregar` para que el código no quede guardado en el historial.
    (canal or get_canal()).enviar_plantilla(telefono, "codigo_acceso", [codigo])
    return True


def validar_codigo(s: Session, estudio_id: int, telefono: str, codigo: str,
                   ahora: datetime) -> str | None:
    """Devuelve un token de sesión nuevo si el código es correcto; si no, None."""
    staff = _staff(s, estudio_id, telefono)
    if staff is None:
        return None
    vigente = s.scalars(select(CodigoAcceso).where(
        CodigoAcceso.staff_id == staff.id, CodigoAcceso.usado.is_(False))
        .order_by(CodigoAcceso.id.desc())).first()
    if vigente is None or a_utc(vigente.expira_en) < ahora or vigente.intentos >= MAX_INTENTOS:
        return None
    if not hmac.compare_digest(vigente.codigo_hash,
                               _hash(f"{staff.id}:{codigo.strip()}")):
        vigente.intentos += 1
        return None
    vigente.usado = True
    token = secrets.token_urlsafe(32)
    s.add(SesionPanel(token_hash=_hash(token), staff_id=staff.id, creado_en=ahora,
                      expira_en=ahora + timedelta(hours=get_settings().panel_sesion_horas)))
    return token


@dataclass
class StaffSesion:
    staff_id: int
    telefono: str


def leer_sesion(s: Session, token: str | None, ahora: datetime) -> StaffSesion | None:
    if not token:
        return None
    ses = s.get(SesionPanel, _hash(token))
    if ses is None or a_utc(ses.expira_en) < ahora:
        return None
    staff = s.get(Staff, ses.staff_id)
    if staff is None or not staff.activo:  # si lo dan de baja, pierde el acceso al momento
        return None
    ses.ultimo_uso_en = ahora
    return StaffSesion(staff.id, staff.telefono)


def cerrar_sesion(s: Session, token: str | None) -> None:
    if token and (ses := s.get(SesionPanel, _hash(token))) is not None:
        s.delete(ses)


def token_csrf(token_sesion: str) -> str:
    """Token CSRF ligado a la sesión: no hace falta guardarlo."""
    return _hash("csrf:" + token_sesion)[:32]


def csrf_valido(token_sesion: str | None, enviado: str | None) -> bool:
    return bool(token_sesion and enviado
                and hmac.compare_digest(token_csrf(token_sesion), enviado))
