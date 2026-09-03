"""Microservicio Adaptador: entrega perfiles al journey de cotización."""
import logging
import time

import pybreaker
from flask import Flask, jsonify
from app.cache import CacheExpiredError, CacheMissError, guardar_perfil, leer_perfil
from app.circuit_breaker import breaker
from app.config import PORT
from app.clientes.open_finance import OpenFinanceClient, OpenFinanceError

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
open_finance = OpenFinanceClient()


@breaker
def _consultar_open_finance(cliente_id):
    return open_finance.obtener_perfil(cliente_id)


@app.get("/health")
def health():
    circuito = breaker.current_state.upper().replace("-", "_")
    return jsonify(status="ok", service="adaptador", circuito=circuito)


@app.get("/perfil/<cliente_id>")
def perfil(cliente_id):
    try:
        perfil_obtenido = _consultar_open_finance(cliente_id)
    except (pybreaker.CircuitBreakerError, OpenFinanceError) as exc:
        instante_deteccion = time.monotonic()
        tipo_falla = (
            "circuito_abierto" if isinstance(exc, pybreaker.CircuitBreakerError) else exc.tipo
        )
        try:
            perfil_cacheado = leer_perfil(cliente_id, instante_deteccion)
        except (CacheMissError, CacheExpiredError) as sin_dato:
            # Condición límite (Fase 0.2): no hay dato que servir, así que es un
            # fallo controlado y no cuenta contra la meta de disponibilidad.
            return (
                jsonify(
                    resultado="fallido",
                    fuente_respuesta="ninguno",
                    hit_miss=sin_dato.hit_miss,
                    tipo_error=sin_dato.tipo_error,
                    condicion_limite=True,
                    mensaje=f"Open Finance no disponible ({tipo_falla}): {sin_dato}",
                ),
                503,
            )
        return jsonify(perfil_cacheado)
    guardar_perfil(cliente_id, perfil_obtenido)
    return jsonify(perfil_obtenido)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
