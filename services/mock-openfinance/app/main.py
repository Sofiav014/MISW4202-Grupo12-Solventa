# Microservicio mock-openfinance — dueño: I1 (Fase 2)
# STUB: por ahora solo modo NORMAL.
#   I1 agrega: latencia SLOW, fallo DOWN, y POST /config para cambiar de modo en caliente.
from flask import Flask, jsonify
from app.config import MODO, PORT

app = Flask(__name__)

# estado mutable en memoria (I1 lo controlará vía /config)
estado = {"modo": MODO}


@app.get("/health")
def health():
    return jsonify(status="ok", service="mock-openfinance", modo=estado["modo"])


@app.get("/perfil/<cliente_id>")
def perfil(cliente_id):
    # STUB: siempre perfil válido. I1 agrega el comportamiento por modo.
    return jsonify(
        cliente_id=cliente_id,
        score_riesgo=720,
        fuente="OPEN_FINANCE",
        timestamp_perfil="2026-08-31T10:00:00Z",
    )


# I1 implementa:
# @app.post("/config")  -> cambia estado["modo"] entre NORMAL / SLOW / DOWN


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
