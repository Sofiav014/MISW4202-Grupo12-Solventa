"""Microservicio Adaptador: entrega perfiles al journey de cotización."""
import logging

import pybreaker
from flask import Flask, jsonify
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
    except pybreaker.CircuitBreakerError:
        # TODO(3.3): fallback a caché en lugar de este 503.
        return jsonify(tipo_error="circuito_abierto", mensaje="Open Finance no disponible temporalmente"), 503
    except OpenFinanceError as exc:
        return jsonify(tipo_error=exc.tipo, mensaje=exc.mensaje), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
