"""Configuración por variables de entorno (constitución §4.4, RF-GEN-002)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./pilates.db"
    redis_url: str = "redis://localhost:6379/0"

    # Claude
    anthropic_api_key: str = ""
    modelo_orquestador: str = "claude-opus-5-5"
    modelo_subagentes: str = "claude-opus-5-5"
    effort_orquestador: Effort = "medium"
    effort_subagentes: Effort = "low"

    # Adaptadores
    canal: Literal["consola", "whatsapp"] = "consola"
    pagos_proveedor: Literal["fake", "stripe"] = "fake"
    facturacion_proveedor: Literal["ninguno", "holded"] = "ninguno"
    stt_proveedor: Literal["fake"] = "fake"

    # WhatsApp
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""

    # Stripe / Holded
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    holded_api_key: str = ""

    estudio_config: Path = Path("config/estudio.toml")
    estudio_id: int = 1  # v1: un estudio por despliegue (RF-GEN-003)
    base_url_publica: str = "http://localhost:8000"
    # Modo demo (RF-DEMO-*): NUNCA en un estudio real
    demo: bool = False

    # Panel web del staff (RF-PAN-001, RNF-SEG-004)
    panel_sesion_horas: int = 12
    panel_codigo_minutos: int = 10

    @field_validator("database_url")
    @classmethod
    def _driver_psycopg(cls, v: str) -> str:
        """Railway/Render/Heroku dan `postgres://`; SQLAlchemy necesita el driver."""
        for prefijo in ("postgres://", "postgresql://"):
            if v.startswith(prefijo):
                return "postgresql+psycopg://" + v[len(prefijo):]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
