"""
Comprobación manual del flujo completo del experimento.

Recorre los tres escenarios contra los servicios en ejecución y muestra, en
cada paso, qué decidió el Detector y qué hizo el Gateway con esa decisión.
Está pensado para verificar a mano que el sistema hace lo que dice hacer,
no para producir las cifras del experimento: de eso se encargan los scripts
de medición.

Requiere los servicios levantados (scripts/levantar_local.py).
"""

import argparse
import sys
from datetime import datetime, timezone
import json
import urllib.error
import urllib.request

import jwt

from deteccion.semilla import generar
from parametros import RADIO_UBICACION_KM, SEMILLA_ALEATORIA, TAMANO_DATASET_SEMILLA


DESPLAZAMIENTO_GRADOS = 6.0
SECRETO = "compose-development-only-change-me"


def _pedir(url, cuerpo=None, cabeceras=None):
    """Ejecuta una petición y devuelve (estado, cuerpo decodificado)."""
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    encabezados = dict(cabeceras or {})

    if datos:
        encabezados["Content-Type"] = "application/json"

    solicitud = urllib.request.Request(url, data=datos, headers=encabezados)

    try:
        with urllib.request.urlopen(solicitud, timeout=5) as respuesta:
            crudo = respuesta.read().decode()
            try:
                return respuesta.status, json.loads(crudo)
            except json.JSONDecodeError:
                return respuesta.status, crudo
    except urllib.error.HTTPError as error:
        crudo = error.read().decode()
        try:
            return error.code, json.loads(crudo)
        except json.JSONDecodeError:
            return error.code, crudo


def _contexto(cliente, device_id, lat, lon):
    """Arma el contexto de sesión que viaja en la cabecera."""
    return {
        "session_id": cliente["session_id"],
        "device_id": device_id,
        "geo_lat": lat,
        "geo_lon": lon,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _escenario(nombre, gateway, detector, cliente, device_id, lat, lon, esperado):
    """Ejecuta un escenario y contrasta lo observado con lo esperado."""
    print(f"\n{'=' * 66}\n{nombre}\n{'=' * 66}")
    print(f"  dispositivo presentado  : {device_id}")
    print(f"  ubicación presentada     : ({lat:.4f}, {lon:.4f})")

    contexto = _contexto(cliente, device_id, lat, lon)

    # 1. Qué decide el Detector por su cuenta.
    _, veredicto = _pedir(f"{detector}/evaluar", {**contexto, "request_id": "validacion"})
    print(f"\n  [1] Detector responde    : {veredicto.get('veredicto')}"
          f"  (motivo: {veredicto.get('motivo')}, score: {veredicto.get('score_riesgo')})")

    # 2. Qué hace el Gateway con esa decisión.
    token = jwt.encode({"sub": "validacion"}, SECRETO, algorithm="HS256")
    estado, cuerpo = _pedir(
        "{}/api/journey/orders".format(gateway),
        cabeceras={"Authorization": f"Bearer {token}",
                   "X-Session-Context": json.dumps(contexto)},
    )
    detalle = cuerpo.get("error_code") if isinstance(cuerpo, dict) and "error_code" in cuerpo else cuerpo
    print(f"  [2] Gateway responde     : {estado}  {detalle}")

    llego = estado == 201
    print(f"  [3] ¿Llegó al journey?   : {'SÍ' if llego else 'NO'}")

    ok = (estado == esperado)
    print(f"\n  Esperado: {esperado}   Observado: {estado}   {'OK' if ok else 'DISCREPANCIA'}")
    return ok


def main():
    """Ejecuta los tres escenarios y resume el resultado."""
    # La consola de Windows no usa UTF-8 por omisión y destrozaría los
    # acentos del informe.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument("--gateway", default="http://127.0.0.1:8080")
    analizador.add_argument("--detector", default="http://127.0.0.1:8001")
    analizador.add_argument("--identidad", default="http://127.0.0.1:8002")
    argumentos = analizador.parse_args()

    cliente = generar(TAMANO_DATASET_SEMILLA, SEMILLA_ALEATORIA)[0]

    print("Cliente tomado del dataset semilla")
    print(f"  sesión              : {cliente['session_id']}")
    print(f"  dispositivo registrado : {cliente['device_id']}")
    print(f"  última ubicación    : ({cliente['lat']:.4f}, {cliente['lon']:.4f})")
    print(f"  radio permitido     : {RADIO_UBICACION_KM} km")

    resultados = [
        _escenario("ESCENARIO 1 — sesión legítima", argumentos.gateway, argumentos.detector,
                   cliente, cliente["device_id"], cliente["lat"], cliente["lon"], esperado=201),
        _escenario("ESCENARIO 2 — dispositivo suplantado", argumentos.gateway, argumentos.detector,
                   cliente, "dispositivo-robado", cliente["lat"], cliente["lon"], esperado=403),
        _escenario("ESCENARIO 3 — ubicación incompatible", argumentos.gateway, argumentos.detector,
                   cliente, cliente["device_id"],
                   cliente["lat"] + DESPLAZAMIENTO_GRADOS, cliente["lon"], esperado=403),
    ]

    # Tras las alertas, la sesión debe haber quedado marcada en Identidad.
    _, sesion = _pedir(f"{argumentos.identidad}/sesiones/validar",
                       {"session_id": cliente["session_id"]})
    print(f"\n{'=' * 66}\nEstado en Identidad tras las alertas\n{'=' * 66}")
    print(f"  estado        : {sesion.get('estado')}")
    print(f"  sesión activa : {sesion.get('sesion_activa')}")

    print(f"\n{'=' * 66}")
    if all(resultados):
        print("RESULTADO: los tres escenarios se comportaron como se esperaba.")
    else:
        print("RESULTADO: hay discrepancias, revisar arriba.")
    print(f"{'=' * 66}")

    return 0 if all(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
