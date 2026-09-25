"""
Levanta los cuatro componentes del experimento como procesos locales.

Alternativa a Docker Compose para desarrollo y demostración: no requiere
tener el demonio de Docker corriendo y arranca en segundos.

    python scripts/levantar_local.py

Deja los servicios en primer plano; Ctrl+C los detiene todos. En otra
terminal se ejecutan los scripts de medición contra ellos.
"""

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


# Cada servicio con el módulo que lo sirve y el puerto donde escucha. Se usa
# 127.0.0.1 en lugar de "localhost" porque en Windows ese nombre resuelve
# primero a ::1 y la espera por IPv6 domina cualquier medición.
SERVICIOS = [
    ("identidad", "identidad.servidor", 8002, {}),
    ("detector", "deteccion.servidor", 8001, {}),
    ("journey", "dobles_http.servidor", 8003, {"SERVICIO_STUB": "journey"}),
    ("gateway", "gateway.servidor", 8080, {}),
]

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATOS = os.path.join(RAIZ, ".datos-locales")


def _entorno():
    """Construye el entorno compartido por todos los servicios."""
    entorno = dict(os.environ)
    entorno.update({
        "SECRETO_JWT": "compose-development-only-change-me",
        "URL_BASE_DATOS_IDENTIDAD": f"sqlite:///{DATOS}/identidad.db".replace("\\", "/"),
        "URL_BASE_DATOS_DETECCION": f"sqlite:///{DATOS}/deteccion.db".replace("\\", "/"),
        "URL_IDENTIDAD": "http://127.0.0.1:8002",
        "URL_DETECTOR": "http://127.0.0.1:8001",
        "URL_JOURNEY": "http://127.0.0.1:8003",
        # La cabecera X-Escenario-Stub permite a un cliente elegir el veredicto.
        # Fuera del perfil de pruebas deterministas debe estar apagada.
        "PERMITIR_ESCENARIO_STUB": "false",
        "PYTHONUNBUFFERED": "1",
    })
    return entorno


def _esperar(nombre, puerto, intentos=40):
    """Sondea /healthz hasta que el servicio responda."""
    url = f"http://127.0.0.1:{puerto}/healthz"

    for _ in range(intentos):
        try:
            with urllib.request.urlopen(url, timeout=1) as respuesta:
                if respuesta.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)

    return False


def main():
    """Carga la semilla, arranca los servicios y espera hasta Ctrl+C."""
    os.makedirs(DATOS, exist_ok=True)
    entorno = _entorno()

    print("Cargando dataset semilla del Detector...")
    semilla = subprocess.run(
        [sys.executable, "-m", "deteccion.semilla"],
        cwd=RAIZ, env=entorno, capture_output=True, text=True,
    )
    if semilla.returncode != 0:
        print(semilla.stderr, file=sys.stderr)
        return 1
    print(f"  {semilla.stdout.strip()}")

    procesos = []
    print("\nArrancando servicios...")

    try:
        for nombre, modulo, puerto, extra in SERVICIOS:
            propio = {**entorno, **extra, "PUERTO": str(puerto)}
            proceso = subprocess.Popen(
                [sys.executable, "-c",
                 f"from {modulo} import app; app.run(host='127.0.0.1', port={puerto}, threaded=True)"],
                cwd=RAIZ, env=propio,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            procesos.append((nombre, proceso))

            estado = "listo" if _esperar(nombre, puerto) else "NO RESPONDE"
            print(f"  {nombre:10} http://127.0.0.1:{puerto}  [{estado}]")

        print("\nTodo arriba. En otra terminal:")
        print("  python scripts/medir_latencia_deteccion.py --requests 100 --scenario legitimo")
        print("\nCtrl+C para detener.\n")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nDeteniendo...")
    finally:
        for nombre, proceso in procesos:
            proceso.terminate()
        for nombre, proceso in procesos:
            try:
                proceso.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proceso.kill()
        print("Servicios detenidos.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
