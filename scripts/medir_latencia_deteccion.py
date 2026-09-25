"""
Mide el tiempo de respuesta de IDetecciónSesión contra su sub-presupuesto.

Es la evidencia que el criterio de aceptación del Detector exige documentar.
Se mide llamando directamente al servicio, sin el Gateway de por medio, para
aislar el costo de la decisión —incluida su consulta a IValidarSesión— del
resto del flujo.
"""

import argparse
from datetime import datetime, timezone
import json
import statistics
import time
import urllib.error
import urllib.request

from deteccion.semilla import generar
from parametros import (
    SEMILLA_ALEATORIA,
    TAMANO_DATASET_SEMILLA,
    UMBRAL_LATENCIA_DETECCION_MS,
)


DESPLAZAMIENTO_GRADOS = 6.0


def _percentil(muestras, percentil):
    """Devuelve el percentil solicitado de una lista de muestras."""
    return round(statistics.quantiles(muestras, n=100)[percentil - 1], 2)


def _cuerpo(cliente, escenario):
    """Construye el contexto de sesión correspondiente al escenario."""
    device_id, lat, lon = cliente["device_id"], cliente["lat"], cliente["lon"]

    if escenario == "suplantado_dispositivo":
        device_id = "dispositivo-robado"
    elif escenario == "suplantado_ubicacion":
        lat = lat + DESPLAZAMIENTO_GRADOS

    return {
        "session_id": cliente["session_id"],
        "device_id": device_id,
        "geo_lat": lat,
        "geo_lon": lon,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": f"medicion-{cliente['session_id']}",
    }


def medir(base, escenario, cantidad, clientes):
    """Ejecuta las evaluaciones y resume latencia y veredictos obtenidos."""
    latencias, veredictos, motivos = [], {}, {}

    for indice in range(cantidad):
        cliente = clientes[indice % len(clientes)]
        datos = json.dumps(_cuerpo(cliente, escenario)).encode()

        solicitud = urllib.request.Request(
            base + "/evaluar", data=datos, headers={"Content-Type": "application/json"})
        inicio = time.perf_counter()

        try:
            with urllib.request.urlopen(solicitud, timeout=5) as respuesta:
                cuerpo = json.loads(respuesta.read())
        except urllib.error.HTTPError as error:
            cuerpo = {"veredicto": f"http_{error.code}", "motivo": None}
            error.read()

        latencias.append((time.perf_counter() - inicio) * 1000)
        veredictos[cuerpo["veredicto"]] = veredictos.get(cuerpo["veredicto"], 0) + 1
        motivos[str(cuerpo.get("motivo"))] = motivos.get(str(cuerpo.get("motivo")), 0) + 1

    p95 = _percentil(latencias, 95)
    detectadas = veredictos.get("sospechoso", 0)

    resumen = {
        "escenario": escenario,
        "peticiones": cantidad,
        "p50_ms": _percentil(latencias, 50),
        "p95_ms": p95,
        "p99_ms": _percentil(latencias, 99),
        "max_ms": round(max(latencias), 2),
        "sub_presupuesto_ms": UMBRAL_LATENCIA_DETECCION_MS,
        "cumple_sub_presupuesto": p95 < UMBRAL_LATENCIA_DETECCION_MS,
        "sobre_presupuesto": sum(1 for l in latencias if l >= UMBRAL_LATENCIA_DETECCION_MS),
        "veredictos": dict(sorted(veredictos.items())),
        "motivos": dict(sorted(motivos.items())),
    }

    if escenario.startswith("suplantado"):
        resumen["tasa_deteccion"] = round(detectadas / cantidad, 4)
    else:
        resumen["tasa_falsos_positivos"] = round(detectadas / cantidad, 4)

    return resumen


def main():
    """Punto de entrada: ejecuta la medición y emite el resumen en JSON."""
    # Se apunta a 127.0.0.1 y no a "localhost": en Windows ese nombre
    # resuelve primero a ::1 y la espera por IPv6 dominaría la medición.
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument("--url", default="http://127.0.0.1:8001")
    analizador.add_argument("--requests", type=int, default=100)
    analizador.add_argument(
        "--scenario",
        choices=("legitimo", "suplantado_dispositivo", "suplantado_ubicacion"),
        default="legitimo",
    )
    argumentos = analizador.parse_args()

    if argumentos.requests < 100:
        analizador.error("--requests debe ser al menos 100 (mínimo por escenario del experimento)")

    clientes = generar(TAMANO_DATASET_SEMILLA, SEMILLA_ALEATORIA)
    print(json.dumps(medir(argumentos.url, argumentos.scenario, argumentos.requests, clientes),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
