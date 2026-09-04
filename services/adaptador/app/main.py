"""Microservicio Adaptador: entrega perfiles al journey de cotización."""
import time

import pybreaker
from flask import Flask, g, jsonify
from app.cache import CacheExpiredError, CacheMissError, guardar_perfil, leer_perfil
from app.circuit_breaker import breaker, registrar_llamada_evitada
from app.config import PORT
from app.clientes.open_finance import OpenFinanceClient, OpenFinanceError
from app.logging_json import (
    TIPO_ERROR_PROVEEDOR,
    configurar_logging,
    instrumentar_peticiones,
)

configurar_logging()

app = Flask(__name__)
open_finance = OpenFinanceClient()


def _estado_circuito():
    return breaker.current_state.upper().replace("-", "_")


instrumentar_peticiones(app, _estado_circuito)


@breaker
def _consultar_open_finance(cliente_id):
    return open_finance.obtener_perfil(cliente_id)


@app.get("/health")
def health():
    return jsonify(status="ok", service="adaptador", circuito=_estado_circuito())


@app.get("/perfil/<cliente_id>")
def perfil(cliente_id):
    if breaker.current_state == "open":
        registrar_llamada_evitada()
    try:
        perfil_obtenido = _consultar_open_finance(cliente_id)
    except (pybreaker.CircuitBreakerError, OpenFinanceError) as exc:
        instante_deteccion = time.monotonic()
        tipo_falla = (
            "circuito_abierto" if isinstance(exc, pybreaker.CircuitBreakerError) else exc.tipo
        )
        g.timestamp_deteccion = instante_deteccion
        try:
            perfil_cacheado = leer_perfil(cliente_id, instante_deteccion)
        except (CacheMissError, CacheExpiredError) as sin_dato:
            # Condición límite (Fase 0.2): no hay dato que servir, así que es un
            # fallo controlado y no cuenta contra la meta de disponibilidad.
            g.timestamp_respuesta_cache = time.monotonic()
            g.hit_miss, g.fuente_respuesta = "MISS", "NONE"
            g.resultado, g.tipo_error = "fallido", sin_dato.tipo_error
            return (
                jsonify(
                    resultado="fallido",
                    fuente_respuesta="NONE",
                    hit_miss=sin_dato.hit_miss,
                    tipo_error=sin_dato.tipo_error,
                    condicion_limite=True,
                    mensaje=f"Open Finance no disponible ({tipo_falla}): {sin_dato}",
                ),
                503,
            )
        g.timestamp_respuesta_cache = time.monotonic()
        g.hit_miss, g.fuente_respuesta = "HIT", "CACHE"
        g.resultado = "degradado"
        g.tipo_error = TIPO_ERROR_PROVEEDOR.get(tipo_falla, "CIRCUIT_OPEN")
        return jsonify(perfil_cacheado)
    guardar_perfil(cliente_id, perfil_obtenido)
    g.hit_miss, g.fuente_respuesta = "N/A", "PROVIDER"
    g.resultado = "exitoso"
    return jsonify(perfil_obtenido)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
