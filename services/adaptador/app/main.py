"""Microservicio Adaptador: entrega perfiles al journey de cotización."""
import logging
import time

import pybreaker
from flask import Flask, jsonify
from app.cache import CacheMissError, leer_perfil
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
        return jsonify(_consultar_open_finance(cliente_id))
    except (pybreaker.CircuitBreakerError, OpenFinanceError) as exc:
        instante_deteccion = time.monotonic()
        tipo_falla = (
            "circuito_abierto" if isinstance(exc, pybreaker.CircuitBreakerError) else exc.tipo
        )
        try:
            return jsonify(leer_perfil(cliente_id, instante_deteccion))
        except CacheMissError:
            return (
                jsonify(
                    tipo_error="cache_miss",
                    mensaje=f"Open Finance no disponible ({tipo_falla}) y no hay perfil en caché",
                ),
                503,
            )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
