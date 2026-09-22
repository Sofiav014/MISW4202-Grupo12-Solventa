import os

from flask import Flask

from .detector_prueba import crear_aplicacion as crear_detector
from .identidad_prueba import crear_aplicacion as crear_identidad
from .journey_prueba import crear_aplicacion as crear_journey

creadores = {"detector": crear_detector, "identidad": crear_identidad, "journey": crear_journey}
app: Flask = creadores[os.environ.get("SERVICIO_STUB", "detector")]()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
