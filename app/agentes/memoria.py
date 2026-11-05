"""Herramienta `memory` (memory_20250818) con backend en PostgreSQL (diseño §6.1).

Rutas virtuales, limitadas por el código al remitente:

    /memories/cliente/<archivo>   memoria del cliente que escribe (lectura y escritura)
    /memories/estudio/<archivo>   memoria global del estudio (lectura; escritura solo admin)

No hay forma de nombrar a otro cliente: `/memories/cliente/` siempre es el remitente.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anthropic.lib.tools import BetaAbstractMemoryTool
from anthropic.types.beta import (
    BetaMemoryTool20250818CreateCommand,
    BetaMemoryTool20250818DeleteCommand,
    BetaMemoryTool20250818InsertCommand,
    BetaMemoryTool20250818RenameCommand,
    BetaMemoryTool20250818StrReplaceCommand,
    BetaMemoryTool20250818ViewCommand,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.modelos import AmbitoMemoria, Memoria
from app.servicios.base import Contexto, Rol

RAIZ = "/memories"
MAX_CARACTERES = 4000  # por archivo, para que la memoria no crezca sin control


class ErrorMemoria(Exception):
    pass


class MemoriaDB(BetaAbstractMemoryTool):
    def __init__(self, ctx: Contexto, abrir_sesion: Callable[[], Any]) -> None:
        super().__init__()
        self.ctx = ctx
        self._abrir = abrir_sesion  # context manager que da una Session transaccional

    # -------------------------------------------------------------- rutas

    def _resolver(self, ruta: str, *, escribir: bool) -> tuple[AmbitoMemoria, str]:
        ruta = ruta.strip().rstrip("/")
        if not ruta.startswith(RAIZ):
            raise ErrorMemoria(f"La ruta debe empezar por {RAIZ}")
        partes = [p for p in ruta[len(RAIZ):].split("/") if p]
        if any(p in (".", "..") for p in partes):
            raise ErrorMemoria("Ruta no válida")
        if not partes:
            raise ErrorMemoria("Indica un archivo dentro de /memories/cliente o /memories/estudio")
        ambito_txt, resto = partes[0], "/".join(partes[1:])
        if ambito_txt == "cliente":
            if self.ctx.cliente_id is None:
                raise ErrorMemoria("Este contacto no tiene ficha; no hay memoria de cliente.")
            return AmbitoMemoria.CLIENTE, resto
        if ambito_txt == "estudio":
            if escribir and self.ctx.rol < Rol.ADMIN:
                raise ErrorMemoria("La memoria del estudio solo la puede modificar el admin.")
            return AmbitoMemoria.ESTUDIO, resto
        raise ErrorMemoria("Solo existen /memories/cliente y /memories/estudio")

    def _consulta(self, ambito: AmbitoMemoria) -> Any:
        q = select(Memoria).where(Memoria.estudio_id == self.ctx.estudio_id,
                                  Memoria.ambito == ambito)
        if ambito == AmbitoMemoria.CLIENTE:
            q = q.where(Memoria.cliente_id == self.ctx.cliente_id)
        else:
            q = q.where(Memoria.cliente_id.is_(None))
        return q

    def _archivo(self, s: Session, ambito: AmbitoMemoria, nombre: str) -> Memoria | None:
        m: Memoria | None = s.scalar(self._consulta(ambito).where(Memoria.ruta == nombre))
        return m

    # -------------------------------------------------------------- comandos

    def _seguro(self, fn: Callable[[], str]) -> str:
        try:
            return fn()
        except ErrorMemoria as e:
            return f"Error: {e}"

    def view(self, command: BetaMemoryTool20250818ViewCommand) -> str:
        def run() -> str:
            ruta = command.path.rstrip("/")
            if ruta in (RAIZ, ""):
                lineas = ["Directorio /memories:"]
                if self.ctx.cliente_id is not None:
                    lineas.append("- /memories/cliente/  (memoria de este cliente)")
                lineas.append("- /memories/estudio/  (información del estudio)")
                return "\n".join(lineas)
            ambito, nombre = self._resolver(ruta, escribir=False)
            with self._abrir() as s:
                if not nombre:
                    archivos = list(s.scalars(self._consulta(ambito).order_by(Memoria.ruta)))
                    if not archivos:
                        return f"{ruta}/ está vacío."
                    return f"Archivos en {ruta}/:\n" + "\n".join(
                        f"- {ruta}/{m.ruta} ({len(m.contenido)} caracteres)" for m in archivos)
                m = self._archivo(s, ambito, nombre)
                if m is None:
                    raise ErrorMemoria(f"No existe {ruta}")
                lineas = m.contenido.splitlines()
                ini, fin = 1, len(lineas)
                if command.view_range:
                    ini = max(command.view_range[0], 1)
                    if len(command.view_range) > 1 and command.view_range[1] != -1:
                        fin = command.view_range[1]
                return "\n".join(f"{i:>4}\t{lineas[i - 1]}" for i in range(ini, fin + 1)
                                 if i <= len(lineas)) or "(archivo vacío)"
        return self._seguro(run)

    def create(self, command: BetaMemoryTool20250818CreateCommand) -> str:
        def run() -> str:
            ambito, nombre = self._resolver(command.path, escribir=True)
            if not nombre:
                raise ErrorMemoria("Indica el nombre del archivo")
            texto = command.file_text[:MAX_CARACTERES]
            with self._abrir() as s:
                m = self._archivo(s, ambito, nombre)
                if m is None:
                    s.add(Memoria(estudio_id=self.ctx.estudio_id, ambito=ambito,
                                  cliente_id=self.ctx.cliente_id
                                  if ambito == AmbitoMemoria.CLIENTE else None,
                                  ruta=nombre, contenido=texto))
                else:
                    m.contenido = texto
            return f"Guardado {command.path}"
        return self._seguro(run)

    def str_replace(self, command: BetaMemoryTool20250818StrReplaceCommand) -> str:
        def run() -> str:
            ambito, nombre = self._resolver(command.path, escribir=True)
            with self._abrir() as s:
                m = self._archivo(s, ambito, nombre)
                if m is None:
                    raise ErrorMemoria(f"No existe {command.path}")
                n = m.contenido.count(command.old_str)
                if n != 1:
                    raise ErrorMemoria(f"El texto a reemplazar aparece {n} veces (debe ser 1)")
                m.contenido = m.contenido.replace(command.old_str, command.new_str)[
                    :MAX_CARACTERES]
            return f"Actualizado {command.path}"
        return self._seguro(run)

    def insert(self, command: BetaMemoryTool20250818InsertCommand) -> str:
        def run() -> str:
            ambito, nombre = self._resolver(command.path, escribir=True)
            with self._abrir() as s:
                m = self._archivo(s, ambito, nombre)
                if m is None:
                    raise ErrorMemoria(f"No existe {command.path}")
                lineas = m.contenido.splitlines()
                pos = max(0, min(command.insert_line, len(lineas)))
                lineas[pos:pos] = command.insert_text.rstrip("\n").splitlines()
                m.contenido = "\n".join(lineas)[:MAX_CARACTERES]
            return f"Insertado en {command.path}"
        return self._seguro(run)

    def delete(self, command: BetaMemoryTool20250818DeleteCommand) -> str:
        def run() -> str:
            ambito, nombre = self._resolver(command.path, escribir=True)
            with self._abrir() as s:
                m = self._archivo(s, ambito, nombre)
                if m is None:
                    raise ErrorMemoria(f"No existe {command.path}")
                s.delete(m)
            return f"Borrado {command.path}"
        return self._seguro(run)

    def rename(self, command: BetaMemoryTool20250818RenameCommand) -> str:
        def run() -> str:
            amb1, n1 = self._resolver(command.old_path, escribir=True)
            amb2, n2 = self._resolver(command.new_path, escribir=True)
            if amb1 != amb2:
                raise ErrorMemoria("No se puede mover entre memoria del cliente y del estudio")
            with self._abrir() as s:
                m = self._archivo(s, amb1, n1)
                if m is None:
                    raise ErrorMemoria(f"No existe {command.old_path}")
                if self._archivo(s, amb2, n2) is not None:
                    raise ErrorMemoria(f"Ya existe {command.new_path}")
                m.ruta = n2
            return f"Renombrado a {command.new_path}"
        return self._seguro(run)


def leer_memoria(s: Session, ctx: Contexto, ambito: AmbitoMemoria) -> str:
    """Volcado para el system prompt (RF-MEM-001, RF-MEM-004)."""
    q = select(Memoria).where(Memoria.estudio_id == ctx.estudio_id, Memoria.ambito == ambito)
    if ambito == AmbitoMemoria.CLIENTE:
        if ctx.cliente_id is None:
            return ""
        q = q.where(Memoria.cliente_id == ctx.cliente_id)
    return "\n\n".join(f"### /memories/{ambito}/{m.ruta}\n{m.contenido}"
                       for m in s.scalars(q.order_by(Memoria.ruta)))
