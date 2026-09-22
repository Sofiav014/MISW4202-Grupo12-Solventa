"""Logging estructurado en JSONL para la instrumentación del experimento."""
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone

from flask import g, request

from app.config import EJECUCION_ID, ESCENARIO, LOG_PATH

# Traduce el tipo interno de OpenFinanceError al valor de la columna tipo_error.
TIPO_ERROR_PROVEEDOR = {
    "timeout": "PROVIDER_TIMEOUT",
    "conexion": "PROVIDER_UNAVAILABLE",
    "respuesta_invalida": "PROVIDER_INVALID_RESPONSE",
}

_CAMPOS_INTERNOS = set(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Serializa cada registro como una línea JSON con sus campos de `extra`."""

    def format(self, record: logging.LogRecord) -> str:
        evento = {
            "ts_wall": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
        }
        evento.update(
            {k: v for k, v in record.__dict__.items() if k not in _CAMPOS_INTERNOS}
        )
        return json.dumps(evento, default=str)


def configurar_logging() -> None:
    """Instala el formatter JSON en stdout y, si hay LOG_PATH, también en archivo."""
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.handlers.clear()

    destinos = [logging.StreamHandler(sys.stdout)]
    if LOG_PATH:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        destinos.append(logging.FileHandler(LOG_PATH, encoding="utf-8"))

    for destino in destinos:
        destino.setFormatter(JsonFormatter())
        raiz.addHandler(destino)


def _ms(desde, hasta):
    """Intervalo en ms entre dos marcas monotónicas, o None si falta alguna."""
    if desde is None or hasta is None:
        return None
    return (hasta - desde) * 1000


def instrumentar_peticiones(app, estado_circuito):
    """Registra un evento con las 17 columnas de instrumentación por cada /perfil.

    Se excluye /health porque no es una petición del experimento. El endpoint
    anota en `g` lo que la instrumentación no puede deducir sola:
    `timestamp_deteccion`, `timestamp_respuesta_cache`, `hit_miss`,
    `fuente_respuesta`, `resultado` y `tipo_error`.
    """
    logger = logging.getLogger("adaptador.request")

    @app.before_request
    def _iniciar_medicion():
        if request.endpoint != "perfil":
            return
        g.request_id = request.headers.get("X-Request-Id") or f"local-{uuid.uuid4()}"
        g.timestamp_inicio = time.monotonic()
        g.estado_circuito_inicio = estado_circuito()

    @app.after_request
    def _emitir_evento(respuesta):
        if "timestamp_inicio" not in g:
            return respuesta

        timestamp_fin = time.monotonic()
        timestamp_deteccion = g.get("timestamp_deteccion")
        timestamp_respuesta_cache = g.get("timestamp_respuesta_cache")
        logger.info(
            "request",
            extra={
                "event_type": "request",
                "request_id": g.request_id,
                "ejecucion_id": EJECUCION_ID,
                "escenario": ESCENARIO,
                "timestamp_inicio": g.timestamp_inicio,
                "timestamp_fin": timestamp_fin,
                "estado_circuito_inicio": g.estado_circuito_inicio,
                "estado_circuito_fin": estado_circuito(),
                "timestamp_deteccion": timestamp_deteccion,
                "timestamp_respuesta_cache": timestamp_respuesta_cache,
                # Solo un circuito ya abierto al entrar evita la llamada; si se
                # abrió con esta petición, el proveedor sí fue invocado.
                "proveedor_invocado": g.estado_circuito_inicio != "OPEN",
                "fuente_respuesta": g.get("fuente_respuesta"),
                "hit_miss": g.get("hit_miss"),
                "latencia_proveedor_ms": _ms(g.timestamp_inicio, timestamp_deteccion),
                "tiempo_conmutacion_ms": _ms(
                    timestamp_deteccion, timestamp_respuesta_cache
                ),
                "latencia_total_ms": _ms(g.timestamp_inicio, timestamp_fin),
                "resultado": g.get("resultado"),
                "tipo_error": g.get("tipo_error"),
            },
        )
        return respuesta
