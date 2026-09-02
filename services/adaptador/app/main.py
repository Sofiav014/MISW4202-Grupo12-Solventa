"""Microservicio Adaptador: entrega perfiles al journey de cotización."""
from flask import Flask, jsonify
from app.config import PORT
from app.clientes.open_finance import OpenFinanceClient, OpenFinanceError

app = Flask(__name__)
open_finance = OpenFinanceClient()


@app.get("/health")
def health():
    # TODO(3.2): reportar el estado real del circuito (CLOSED/OPEN/HALF_OPEN).
    return jsonify(status="ok", service="adaptador", circuito="CLOSED")


@app.get("/perfil/<cliente_id>")
def perfil(cliente_id):
    try:
        return jsonify(open_finance.obtener_perfil(cliente_id))
    except OpenFinanceError as exc:
        # TODO(3.2/3.5): sustituir por Circuit Breaker + fallback a caché.
        return jsonify(tipo_error=exc.tipo, mensaje=exc.mensaje), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
