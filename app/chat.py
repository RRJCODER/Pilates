"""Chat por consola con el agente, sin WhatsApp (T2.5).

    python -m app.chat --como +34600000001          # como admin del estudio de ejemplo
    python -m app.chat --como +34612345678          # como contacto nuevo

Comandos: `:como <tel>` cambia de remitente · `:pagar <ref>` simula el pago de un
enlace · `:cron <tarea>` lanza una tarea del cron · `:uso` tokens del último turno ·
`:salir`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re

from app.conversacion import entregar, procesar_mensaje
from app.db.sesion import sesion
from app.integraciones.canal import CanalConsola, set_canal
from app.servicios.pagos import confirmar_pago_proveedor
from config.settings import get_settings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--como", required=True, help="Teléfono del remitente (+34…)")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING)

    cfg = get_settings()
    canal = CanalConsola()
    set_canal(canal)
    tel = args.como
    ultimo_uso: object = None
    print(f"💬 Hablando como {tel}. Escribe :salir para terminar.")
    while True:
        try:
            texto = input(f"\n[{tel}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not texto:
            continue
        if texto == ":salir":
            break
        if texto.startswith(":como "):
            tel = texto.split(maxsplit=1)[1]
            continue
        if texto == ":uso":
            print(ultimo_uso)
            continue
        if texto.startswith(":pagar"):
            ref = texto.split(maxsplit=1)[1] if " " in texto else ""
            ref = re.sub(r".*/dev/pagar/", "", ref)
            from datetime import UTC, datetime

            with sesion() as s:
                res = confirmar_pago_proveedor(s, ref, datetime.now(UTC))
            entregar(cfg.estudio_id, res["avisos"], canal)
            continue
        if texto.startswith(":cron "):
            import app.worker as w

            tarea = getattr(w, texto.split(maxsplit=1)[1], None)
            if tarea is None:
                print("Tareas: cada_15, diario_manana, diario_tarde, semanal, resumir")
            else:
                asyncio.run(tarea({}))
            continue
        resultado = procesar_mensaje(cfg.estudio_id, tel, texto, canal=canal)
        ultimo_uso = resultado.uso


if __name__ == "__main__":
    main()
