# Microservicio adaptador — dueño: I2 (resiliencia) + I4 (caché)
# STUB: por ahora reenvía al mock sin timeout/breaker/caché.
#   I2 (Fase 3): timeout 700 ms + Circuit Breaker (pybreaker), expone estado en /health
#   I4 (Fase 3): fallback a Redis + write-back + cache miss
import requests
from flask import Flask, jsonify
from app.config import MOCK_URL, PORT

app = Flask(__name__)


@app.get("/health")
def health():
    # I2 expone aquí el estado real del circuito (CLOSED/OPEN/HALF_OPEN)
    return jsonify(status="ok", service="adaptador", circuito="CLOSED")


@app.get("/perfil/<cliente_id>")
def perfil(cliente_id):
    # STUB: llamada directa sin resiliencia
    resp = requests.get(f"{MOCK_URL}/perfil/{cliente_id}", timeout=5)
    return jsonify(resp.json())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
