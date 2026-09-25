"""
Siembra en ms-identidad-sesiones la misma población que el Detector conoce.

El Detector y el servicio de Identidad mantienen bases de datos separadas, y
`deteccion.semilla` solo carga la del Detector. Si Identidad no conoce las
sesiones, `IValidarSesión` responde 200 con `device_id_registrado: null` —no un
error— y el Detector cae silenciosamente a su historial local. La corrida
entonces mide un camino degradado sin que nada lo delate, así que esta siembra
es condición previa a cualquier medición.

Además recoge el JWT que Identidad emite por sesión: es la credencial con la
que el generador de tráfico autentica sus peticiones ante el Gateway.

    python -m generador.sembrar_identidad --url http://127.0.0.1:8002 --verificar
"""

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import random
import sys

import requests

from deteccion.semilla import generar
from parametros import SEMILLA_ALEATORIA, TAMANO_DATASET_SEMILLA


# Ubicación del artefacto con las credenciales. El locustfile lo lee desde
# aquí, así que el orquestador y el generador comparten esta ruta.
CAMINO_CREDENCIALES_POR_DEFECTO = Path("resultados") / "credenciales.json"

# Vigencia de las sesiones sembradas. Se toma holgada a propósito: una suite de
# varios escenarios por varias repeticiones no debe ver expirar los tokens a
# mitad de la medición, porque un 401 no es un veredicto del Detector y
# contaminaría la matriz de confusión.
TTL_SESION_SEGUNDOS = 7200

# Cuántas sesiones se comprueban contra IValidarSesión tras sembrar. No hace
# falta revisarlas todas: la siembra es una operación uniforme, y un error de
# conexión o de contrato se manifiesta en la primera.
MUESTRAS_A_VERIFICAR = 10


class ErrorSiembra(RuntimeError):
    """La población no quedó sembrada como el experimento necesita."""


@dataclass(frozen=True)
class Credencial:
    """
    Una sesión sembrada, con su contexto legítimo y su token.

    Reúne lo que el generador de tráfico necesita para construir una petición
    legítima —dispositivo y ubicación registrados— y el JWT con el que
    autenticarla.
    """

    session_id: str
    cliente_id: str
    device_id: str
    lat: float
    lon: float
    token: str
    expira_en: str | None


def sembrar(
    url_identidad: str,
    cantidad: int = TAMANO_DATASET_SEMILLA,
    semilla: int = SEMILLA_ALEATORIA,
    ttl_segundos: int = TTL_SESION_SEGUNDOS,
    tiempo_espera_s: float = 5.0,
) -> list[Credencial]:
    """
    Crea una sesión activa en Identidad por cada cliente de la población.

    La población se obtiene de `deteccion.semilla.generar`, de modo que ambos
    servicios compartan exactamente los mismos `session_id` y `device_id`. El
    endpoint hace *upsert*: volver a sembrar devuelve las sesiones al estado
    `activa` y descarta cualquier `pendiente_verificacion` que una corrida
    anterior haya dejado.

    Args:
        url_identidad: URL base de ms-identidad-sesiones.
        cantidad: Número de clientes a sembrar.
        semilla: Semilla de la población; debe ser la misma que usó el Detector.
        ttl_segundos: Vigencia de cada sesión y de su token.
        tiempo_espera_s: Espera máxima por cada respuesta de Identidad.

    Returns:
        Las credenciales sembradas, en el orden de la población.

    Raises:
        ErrorSiembra: Si Identidad no responde o rechaza alguna sesión.
    """
    base = url_identidad.rstrip("/")
    credenciales = []

    for cliente in generar(cantidad, semilla):
        cuerpo = {
            "session_id": cliente["session_id"],
            "device_id": cliente["device_id"],
            "ttl_segundos": ttl_segundos,
        }

        try:
            respuesta = requests.post(f"{base}/sesiones", json=cuerpo, timeout=tiempo_espera_s)
        except requests.RequestException as error:
            raise ErrorSiembra(
                f"No se pudo contactar Identidad en {base} para "
                f"{cliente['session_id']}: {error}"
            ) from error

        # Identidad responde 201 al crear o renovar. Cualquier otro código
        # significa que la sesión no quedó activa, y seguir sembrando solo
        # aplazaría el fallo hasta la medición.
        if respuesta.status_code != 201:
            raise ErrorSiembra(
                f"Identidad rechazó {cliente['session_id']} "
                f"(HTTP {respuesta.status_code}): {respuesta.text[:200]}"
            )

        datos = respuesta.json()

        credenciales.append(Credencial(
            session_id=cliente["session_id"],
            cliente_id=cliente["cliente_id"],
            device_id=cliente["device_id"],
            lat=cliente["lat"],
            lon=cliente["lon"],
            token=datos["token"],
            expira_en=datos.get("expira_en"),
        ))

    return credenciales


def verificar(
    url_identidad: str,
    credenciales: list[Credencial],
    muestras: int = MUESTRAS_A_VERIFICAR,
    semilla: int = SEMILLA_ALEATORIA,
    tiempo_espera_s: float = 5.0,
) -> None:
    """
    Comprueba contra IValidarSesión que la siembra sirve para medir.

    Consulta el mismo endpoint que usa el Detector y exige que devuelva el
    dispositivo registrado y la sesión activa. Es la barrera que impide correr
    el experimento contra el camino degradado: sin esta comprobación, una
    siembra incompleta no produce ningún síntoma observable y las métricas
    saldrían de un flujo distinto del que el diseño describe.

    Args:
        url_identidad: URL base de ms-identidad-sesiones.
        credenciales: Credenciales devueltas por `sembrar`.
        muestras: Cuántas sesiones comprobar, además de la primera y la última.
        semilla: Semilla del muestreo, para que la comprobación sea repetible.
        tiempo_espera_s: Espera máxima por cada respuesta.

    Raises:
        ErrorSiembra: Si alguna sesión no está activa o no reporta su dispositivo.
    """
    if not credenciales:
        raise ErrorSiembra("No hay credenciales que verificar: la población está vacía.")

    base = url_identidad.rstrip("/")

    # Se incluyen los extremos porque un fallo parcial de la siembra suele
    # cortarse al final de la población, y un muestreo puramente aleatorio
    # podría no alcanzarlo.
    indices = {0, len(credenciales) - 1}
    aleatorio = random.Random(semilla)
    while len(indices) < min(muestras, len(credenciales)):
        indices.add(aleatorio.randrange(len(credenciales)))

    for indice in sorted(indices):
        esperada = credenciales[indice]

        try:
            respuesta = requests.post(
                f"{base}/sesiones/validar",
                json={"session_id": esperada.session_id},
                timeout=tiempo_espera_s,
            )
        except requests.RequestException as error:
            raise ErrorSiembra(
                f"IValidarSesión no respondió para {esperada.session_id}: {error}"
            ) from error

        if respuesta.status_code != 200:
            raise ErrorSiembra(
                f"IValidarSesión devolvió HTTP {respuesta.status_code} "
                f"para {esperada.session_id}."
            )

        datos = respuesta.json()

        # Este es el punto que importa: si el dispositivo llega vacío, el
        # Detector usará su historial local en vez del dato de Identidad.
        if datos.get("device_id_registrado") != esperada.device_id:
            raise ErrorSiembra(
                f"{esperada.session_id}: Identidad reporta "
                f"device_id_registrado={datos.get('device_id_registrado')!r}, "
                f"se esperaba {esperada.device_id!r}. La siembra no sirve para medir."
            )

        if not datos.get("sesion_activa"):
            raise ErrorSiembra(
                f"{esperada.session_id}: la sesión no está activa "
                f"(estado={datos.get('estado')!r})."
            )


def guardar(credenciales: list[Credencial], destino: Path) -> Path:
    """
    Escribe el artefacto de credenciales que consume el generador de tráfico.

    Args:
        credenciales: Credenciales a persistir.
        destino: Ruta del archivo JSON; sus directorios se crean si faltan.

    Returns:
        La ruta escrita.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    contenido = {
        "version": 1,
        "credenciales": [asdict(credencial) for credencial in credenciales],
    }

    destino.write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")

    return destino


def cargar(origen: Path) -> list[Credencial]:
    """
    Lee el artefacto de credenciales.

    Args:
        origen: Ruta del archivo escrito por `guardar`.

    Returns:
        Las credenciales almacenadas.

    Raises:
        ErrorSiembra: Si el archivo no existe, no se puede leer o está vacío.
    """
    origen = Path(origen)

    if not origen.is_file():
        raise ErrorSiembra(
            f"No existe {origen}. Ejecuta primero "
            f"'python -m generador.sembrar_identidad'."
        )

    try:
        contenido = json.loads(origen.read_text(encoding="utf-8"))
        filas = contenido["credenciales"]
    except (json.JSONDecodeError, KeyError, TypeError, OSError) as error:
        raise ErrorSiembra(f"{origen} no tiene el formato esperado: {error}") from error

    if not filas:
        raise ErrorSiembra(f"{origen} no contiene credenciales.")

    return [Credencial(**fila) for fila in filas]


def main(argv=None) -> int:
    """Punto de entrada: siembra Identidad y, si se pide, verifica el resultado."""
    analizador = argparse.ArgumentParser(
        description="Siembra en Identidad la población que el Detector conoce."
    )
    analizador.add_argument("--url", default="http://127.0.0.1:8002",
                            help="URL base de ms-identidad-sesiones.")
    analizador.add_argument("--cantidad", type=int, default=TAMANO_DATASET_SEMILLA)
    analizador.add_argument("--semilla", type=int, default=SEMILLA_ALEATORIA)
    analizador.add_argument("--ttl", type=int, default=TTL_SESION_SEGUNDOS,
                            help="Vigencia en segundos de cada sesión y su token.")
    analizador.add_argument("--salida", type=Path, default=CAMINO_CREDENCIALES_POR_DEFECTO)
    analizador.add_argument("--verificar", action="store_true",
                            help="Comprueba la siembra contra IValidarSesión.")
    argumentos = analizador.parse_args(argv)

    try:
        credenciales = sembrar(
            argumentos.url,
            cantidad=argumentos.cantidad,
            semilla=argumentos.semilla,
            ttl_segundos=argumentos.ttl,
        )

        if argumentos.verificar:
            verificar(argumentos.url, credenciales, semilla=argumentos.semilla)

    except ErrorSiembra as error:
        print(f"Siembra de Identidad fallida: {error}", file=sys.stderr)
        return 1

    destino = guardar(credenciales, argumentos.salida)

    comprobacion = " y verificadas" if argumentos.verificar else ""
    print(
        f"Identidad sembrada{comprobacion}: {len(credenciales)} sesiones "
        f"(semilla={argumentos.semilla}, ttl={argumentos.ttl}s)"
    )
    print(f"  Credenciales en {destino}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
