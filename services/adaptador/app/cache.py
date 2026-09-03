"""Fallback a caché de Redis cuando Open Finance no responde, y write-back
cuando responde bien."""
import json
import logging
import time
from datetime import datetime, timezone

import redis

from app.config import REDIS_HOST, REDIS_PORT, TTL_S

logger = logging.getLogger("adaptador.cache")

_cliente_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


class CacheMissError(Exception):
    """El cliente no tiene perfil en la caché.

    `tipo_error` y `hit_miss` son los valores que la instrumentación registra
    en las columnas homónimas.
    """

    tipo_error = "CACHE_MISS"
    hit_miss = "MISS"


class CacheExpiredError(Exception):
    """El perfil está en la caché, pero venció."""

    tipo_error = "CACHE_EXPIRED"
    hit_miss = "EXPIRED"


def _clave(cliente_id: str) -> str:
    return f"perfil:{cliente_id}"


def _esta_vencido(perfil: dict) -> bool:
    """Indica si el perfil superó TTL_S según su propio `timestamp_perfil`.

    Redis borra la key al vencer, así que sin este chequeo un perfil expirado
    sería indistinguible de uno que nunca existió. Un timestamp ausente o
    ilegible se trata como fresco.
    """
    marca = perfil.get("timestamp_perfil")
    if not isinstance(marca, str):
        return False
    try:
        emitido = datetime.fromisoformat(marca.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - emitido).total_seconds() > TTL_S


def leer_perfil(cliente_id: str, instante_deteccion: float) -> dict:
    valor = _cliente_redis.get(_clave(cliente_id))
    instante_lectura = time.monotonic()

    perfil = json.loads(valor) if valor is not None else None
    if perfil is None:
        hit_miss = "MISS"
    elif _esta_vencido(perfil):
        hit_miss = "EXPIRED"
    else:
        hit_miss = "HIT"

    logger.info(
        "cache_fallback hit_miss=%s cliente_id=%s instante_deteccion=%s instante_lectura=%s",
        hit_miss,
        cliente_id,
        instante_deteccion,
        instante_lectura,
        extra={
            "hit_miss": hit_miss,
            "cliente_id": cliente_id,
            "instante_deteccion": instante_deteccion,
            "instante_lectura": instante_lectura,
        },
    )

    if hit_miss == "MISS":
        raise CacheMissError(f"No hay perfil cacheado para cliente_id={cliente_id}")
    if hit_miss == "EXPIRED":
        raise CacheExpiredError(
            f"El perfil cacheado de cliente_id={cliente_id} venció (TTL_S={TTL_S}s)"
        )

    return perfil


def guardar_perfil(cliente_id: str, perfil: dict) -> None:
    perfil_cacheado = {**perfil, "fuente": "CACHE"}
    _cliente_redis.set(_clave(cliente_id), json.dumps(perfil_cacheado), ex=TTL_S)

    logger.info(
        "cache_write cliente_id=%s ttl_s=%s",
        cliente_id,
        TTL_S,
        extra={"cliente_id": cliente_id, "ttl_s": TTL_S},
    )
