"""Fallback a caché de Redis cuando Open Finance no responde, y write-back
cuando responde bien."""
import json
import logging
import time

import redis

from app.config import REDIS_HOST, REDIS_PORT, TTL_S

logger = logging.getLogger("adaptador.cache")

_cliente_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


class CacheMissError(Exception):
    """No hay perfil cacheado para este cliente."""


def _clave(cliente_id: str) -> str:
    return f"perfil:{cliente_id}"


def leer_perfil(cliente_id: str, instante_deteccion: float) -> dict:
    valor = _cliente_redis.get(_clave(cliente_id))
    instante_lectura = time.monotonic()

    resultado = "hit" if valor is not None else "miss"
    logger.info(
        "cache_fallback resultado=%s cliente_id=%s instante_deteccion=%s instante_lectura=%s",
        resultado,
        cliente_id,
        instante_deteccion,
        instante_lectura,
        extra={
            "resultado": resultado,
            "cliente_id": cliente_id,
            "instante_deteccion": instante_deteccion,
            "instante_lectura": instante_lectura,
        },
    )

    if valor is None:
        raise CacheMissError(f"No hay perfil cacheado para cliente_id={cliente_id}")

    return json.loads(valor)


def guardar_perfil(cliente_id: str, perfil: dict) -> None:
    perfil_cacheado = {**perfil, "fuente": "CACHE"}
    _cliente_redis.set(_clave(cliente_id), json.dumps(perfil_cacheado), ex=TTL_S)

    logger.info(
        "cache_write cliente_id=%s ttl_s=%s",
        cliente_id,
        TTL_S,
        extra={"cliente_id": cliente_id, "ttl_s": TTL_S},
    )
