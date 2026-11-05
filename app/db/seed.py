"""Carga un estudio desde `config/estudio.toml` (RF-GEN-001, T0.5).

Uso:
    python -m app.db.seed                 # estudio y horario de las próximas 2 semanas
    python -m app.db.seed --demo-clientes 30
"""

from __future__ import annotations

import argparse
import random
import tomllib
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.modelos import (
    AmbitoMemoria,
    Base,
    Bono,
    Clase,
    Cliente,
    EstadoBono,
    Estudio,
    Memoria,
    Producto,
    RolStaff,
    Staff,
    TipoClase,
)
from app.db.sesion import get_engine, sesion
from config.settings import get_settings


def cargar_config(ruta: Path) -> dict[str, Any]:
    with ruta.open("rb") as f:
        return tomllib.load(f)


def crear_estudio(s: Session, cfg: dict[str, Any]) -> Estudio:
    e = cfg["estudio"]
    if s.scalar(select(Estudio).where(Estudio.nombre == e["nombre"])):
        raise SystemExit(f"El estudio «{e['nombre']}» ya existe; no se vuelve a cargar.")
    estudio = Estudio(
        nombre=e["nombre"],
        zona_horaria=e.get("zona_horaria", "Europe/Madrid"),
        config={"estudio": e, "politicas": cfg.get("politicas", {})},
    )
    s.add(estudio)
    s.flush()

    for st in cfg.get("staff", []):
        s.add(
            Staff(
                estudio_id=estudio.id,
                nombre=st["nombre"],
                telefono=st["telefono"],
                rol=RolStaff(st["rol"]),
            )
        )
    for tc in cfg.get("tipos_clase", []):
        s.add(TipoClase(estudio_id=estudio.id, **tc))
    for p in cfg.get("productos", []):
        s.add(
            Producto(
                estudio_id=estudio.id,
                clave=p["clave"],
                nombre=p["nombre"],
                tipo=p["tipo"],
                sesiones=p["sesiones"],
                validez_dias=p["validez_dias"],
                precio=Decimal(str(p["precio"])),
            )
        )
    for ruta, contenido in cfg.get("faq", {}).items():
        s.add(
            Memoria(
                estudio_id=estudio.id,
                ambito=AmbitoMemoria.ESTUDIO,
                cliente_id=None,
                ruta=ruta,
                contenido=contenido.strip(),
            )
        )
    s.flush()
    return estudio


def generar_clases(
    s: Session, estudio: Estudio, cfg: dict[str, Any], desde: date, semanas: int = 2
) -> int:
    tz = ZoneInfo(estudio.zona_horaria)
    tipos = {
        t.clave: t for t in s.scalars(select(TipoClase).where(TipoClase.estudio_id == estudio.id))
    }
    staff = {p.nombre: p for p in s.scalars(select(Staff).where(Staff.estudio_id == estudio.id))}
    n = 0
    for d in range(semanas * 7):
        dia = desde + timedelta(days=d)
        for h in cfg.get("horario", []):
            if dia.weekday() not in h["dias"]:
                continue
            tipo = tipos[h["tipo"]]
            hh, mm = map(int, h["hora"].split(":"))
            inicio = datetime.combine(dia, time(hh, mm), tzinfo=tz)
            s.add(
                Clase(
                    estudio_id=estudio.id,
                    tipo_id=tipo.id,
                    instructor_id=staff[h["instructor"]].id,
                    inicio=inicio,
                    fin=inicio + timedelta(minutes=tipo.duracion_min),
                    aforo=tipo.aforo,
                )
            )
            n += 1
    return n


NOMBRES = [
    "Lucía",
    "María",
    "Paula",
    "Carmen",
    "Sofía",
    "Elena",
    "Laura",
    "Marta",
    "Sara",
    "Andrea",
    "Julia",
    "Irene",
    "Pedro",
    "Javier",
    "Pablo",
    "Raquel",
    "Nuria",
    "Cristina",
    "Alba",
    "Silvia",
]
APELLIDOS = ["García", "López", "Martín", "Sánchez", "Pérez", "Gómez", "Ruiz", "Díaz", "Moreno"]


def crear_clientes_demo(s: Session, estudio: Estudio, n: int, semilla: int = 7) -> None:
    rnd = random.Random(semilla)
    productos = list(
        s.scalars(
            select(Producto).where(Producto.estudio_id == estudio.id, Producto.tipo != "suelta")
        )
    )
    hoy = date.today()
    for i in range(n):
        c = Cliente(
            estudio_id=estudio.id,
            nombre=f"{rnd.choice(NOMBRES)} {rnd.choice(APELLIDOS)}",
            telefono=f"+3461{i:07d}",
            nivel=rnd.choice(["inicial", "intermedio"]),
            consent_marketing=rnd.random() < 0.7,
        )
        s.add(c)
        s.flush()
        p = rnd.choice(productos)
        s.add(
            Bono(
                estudio_id=estudio.id,
                cliente_id=c.id,
                producto_id=p.id,
                sesiones_total=p.sesiones,
                sesiones_usadas=rnd.randint(0, p.sesiones - 1),
                inicio=hoy - timedelta(days=10),
                caduca=hoy + timedelta(days=p.validez_dias),
                estado=EstadoBono.ACTIVO,
            )
        )


def seed(engine: Engine, ruta_cfg: Path, demo_clientes: int = 0, desde: date | None = None) -> int:
    Base.metadata.create_all(engine)
    cfg = cargar_config(ruta_cfg)
    with sesion(engine) as s:
        estudio = crear_estudio(s, cfg)
        generar_clases(s, estudio, cfg, desde or date.today())
        if demo_clientes:
            crear_clientes_demo(s, estudio, demo_clientes)
        return estudio.id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=get_settings().estudio_config)
    ap.add_argument("--demo-clientes", type=int, default=0)
    args = ap.parse_args()
    estudio_id = seed(get_engine(), args.config, args.demo_clientes)
    print(f"Estudio {estudio_id} cargado desde {args.config}")


if __name__ == "__main__":
    main()
