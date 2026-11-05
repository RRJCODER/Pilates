"""Cola de mensajes entrantes (arq + Redis).

El webhook responde 200 a Meta en milisegundos y el worker procesa después.
Sin Redis (desarrollo), se procesa en segundo plano en el propio proceso.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from config.settings import get_settings

log = logging.getLogger(__name__)
_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis | None:
    global _pool
    if _pool is None:
        try:
            _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        except Exception:
            log.warning("Redis no disponible: se procesará en el propio proceso")
            return None
    return _pool


async def encolar_mensaje(mensaje: dict[str, Any]) -> None:
    pool = await _get_pool()
    if pool is not None:
        # _job_id = message_id: arq no encola dos veces el mismo trabajo (RNF-FIA-001)
        await pool.enqueue_job("procesar_whatsapp", mensaje,
                               _job_id=f"wa:{mensaje['wa_message_id']}")
        return
    from app.worker import procesar_whatsapp_sync

    asyncio.get_running_loop().run_in_executor(None, procesar_whatsapp_sync, mensaje)
