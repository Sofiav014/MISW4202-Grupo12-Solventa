"""Fallback a caché de Redis cuando Open Finance no responde."""
import json
import logging
import time

import redis

from app.config import REDIS_HOST, REDIS_PORT

logger = logging.getLogger("adaptador.cache")

_cliente_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


class CacheMissError(Exception):
    """No hay perfil cacheado para este cliente."""


def _clave(cliente_id: str) -> str:
    return f"perfil:{cliente_id}"


def leer_perfil(cliente_id: str, instante_deteccion: float) -> dict:
    """Lee el perfil cacheado tras detectar una falla de Open Finance.

    `instante_deteccion` es el time.monotonic() del momento en que se
    detectó la falla (timeout, error o circuito abierto) - se captura en
    main.py, justo donde ocurre. Aquí se toma el instante de lectura y se
    registran ambos crudos; el cálculo del tiempo de conmutación como
    métrica del experimento se hace en 4.2, no aquí.
    """
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
