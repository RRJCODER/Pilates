"""T0.4 / T0.5: el esquema se crea y el estudio de ejemplo se carga (RF-GEN-001, RF-GEN-003)."""

from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, func, select

from app.db.modelos import Base, Bono, Clase, Cliente, Memoria, Producto, Staff
from app.db.seed import seed
from app.db.sesion import sesion

CFG = Path(__file__).parents[2] / "config" / "estudio.toml"


def test_seed_carga_estudio_de_ejemplo() -> None:
    engine = create_engine("sqlite://")
    # Lunes 2 de noviembre de 2026: 2 semanas completas de horario
    estudio_id = seed(engine, CFG, demo_clientes=30, desde=date(2026, 11, 2))

    with sesion(engine) as s:
        assert s.scalar(select(func.count()).select_from(Staff)) == 4
        assert s.scalar(select(func.count()).select_from(Producto)) == 5
        # Horario de ejemplo: 3+3+4+4+2+2+1 = 19 clases/semana
        assert s.scalar(select(func.count()).select_from(Clase)) == 38
        assert s.scalar(select(func.count()).select_from(Cliente)) == 30
        assert s.scalar(select(func.count()).select_from(Bono)) == 30
        assert s.scalar(select(func.count()).select_from(Memoria)) == 3
        # Todas las tablas de negocio van asociadas al estudio (RF-GEN-003)
        assert {c.estudio_id for c in s.scalars(select(Clase))} == {estudio_id}


def test_todas_las_tablas_de_negocio_tienen_estudio_id() -> None:
    sin_estudio = {"estudio", "mensajes", "resumenes", "eventos_agente"}
    for nombre, tabla in Base.metadata.tables.items():
        if nombre not in sin_estudio:
            assert "estudio_id" in tabla.c, nombre
