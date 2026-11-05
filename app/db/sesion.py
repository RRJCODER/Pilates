"""Motor y sesiones de base de datos."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


@contextmanager
def sesion(engine: Engine | None = None) -> Iterator[Session]:
    """Sesión transaccional: hace commit al salir y rollback si hay error."""
    factory = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
    with factory() as s, s.begin():
        yield s
