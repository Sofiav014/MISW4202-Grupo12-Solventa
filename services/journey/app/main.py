# Microservicio journey — dueño: I3
# Punto de entrada del experimento. Orquesta el recorrido de cotización.
import uuid
from flask import Flask,jsonify,request
import requests
from app.config import ADAPTADOR_URL, PORT

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify(status="ok", service="journey")


@app.post("/cotizar")
def cotizar():
    request_id = str(uuid.uuid4())
    datos = request.get_json(silent=True) or {}
    # Pide el perfil al adaptador (que decide: proveedor o caché)
    cliente_id = datos.get("cliente_id", "12345")
    resp = requests.get(f"{ADAPTADOR_URL}/perfil/{cliente_id}", timeout=5)
    if resp.status_code != 200:
            detalle = resp.json()
            return jsonify(
                request_id=request_id,
                resultado="fallido",
                tipo_error=detalle.get("tipo_error", "error_adaptador"),
            ), 503
    perfil = resp.json()
    # Stub: prima constante, sin lógica actuarial real
    return jsonify(
        request_id=request_id,
        prima=100,
        fuente_perfil=perfil["fuente"],
        resultado="exitoso",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
