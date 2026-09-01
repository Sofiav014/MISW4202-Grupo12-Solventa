# Microservicio journey — dueño: I3
# Punto de entrada del experimento. Orquesta el recorrido de cotización.
import uuid
import requests
from flask import Flask, jsonify
from app.config import ADAPTADOR_URL, PORT

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify(status="ok", service="journey")


@app.post("/cotizar")
def cotizar():
    request_id = str(uuid.uuid4())
    # Pide el perfil al adaptador (que decide: proveedor o caché)
    resp = requests.get(f"{ADAPTADOR_URL}/perfil/12345", timeout=5)
    perfil = resp.json()
    # Stub: prima constante, sin lógica actuarial real
    return jsonify(
        request_id=request_id,
        prima=100,
        fuente_perfil=perfil.get("fuente", "DESCONOCIDA"),
        resultado="exitoso",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
