"""
Orquestador de corridas del experimento de seguridad (HA16).

Ejecuta uno o varios escenarios contra los servicios en marcha y deja, por cada
repetición, el manifest de sus condiciones y el registro por petición del que
sale la matriz de confusión.

    python -m generador.run_escenario --escenario TODOS --repeticiones 3

Requiere los servicios levantados (`python scripts/levantar_local.py`) o el
stack de Compose en marcha.
"""

import argparse
import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys

from parametros import SEMILLA_ALEATORIA, TAMANO_DATASET_SEMILLA

from . import manifest as manifest_mod
from .escenarios import ESCENARIOS, descripcion, resolver
from .registro import NOMBRE_ARCHIVO as NOMBRE_REGISTRO
from .sembrar_identidad import (
    CAMINO_CREDENCIALES_POR_DEFECTO,
    TTL_SESION_SEGUNDOS,
    ErrorSiembra,
    guardar as guardar_credenciales,
    sembrar,
    verificar,
)

RAIZ = Path(__file__).resolve().parent.parent
LOCUSTFILE = RAIZ / "generador" / "locustfile.py"

_PATRON_DURACION = re.compile(r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s?)?$")

# Duración máxima de una corrida medida. Más allá de este punto los clientes
# sembrados empiezan a salir de la ventana de actividad reciente del Detector:
# la regla de ubicación deja de aplicarse y las suplantaciones por ubicación
# pasan a contarse como falsos negativos que son un artefacto de la duración.
DURACION_MAXIMA_S = 120

# Escenario con que se calienta, siempre el mismo. Sus peticiones se rechazan
# por dispositivo, así que nunca reescriben la última ubicación conocida y el
# estado geográfico llega intacto a la medición.
ESCENARIO_CALENTAMIENTO = "B"


def segundos_desde_duracion(cadena: str) -> int:
    """Convierte '90s', '2m' o '1h30m' a segundos."""
    coincidencia = _PATRON_DURACION.fullmatch(cadena.strip())

    if not coincidencia or not any(coincidencia.groups()):
        raise ValueError(f"duración inválida: {cadena!r}")

    horas, minutos, segundos = (int(g) if g else 0 for g in coincidencia.groups())

    return horas * 3600 + minutos * 60 + segundos


def resembrar(
    url_identidad: str,
    cantidad: int,
    semilla: int,
    ttl: int,
    destino: Path,
    url_base_datos_deteccion: str | None = None,
    url_detector: str | None = None,
    servicio_compose: str | None = None,
) -> Path:
    """
    Restaura el estado de partida de Detector e Identidad.

    No es una optimización sino una condición de validez, y se comprobó
    midiendo: sin resembrar, las sesiones que un escenario anterior dejó en
    `pendiente_verificacion` —o cuya línea base arrastró tras un veredicto
    normal— producen falsos positivos en el escenario legítimo que son un
    artefacto del orden de las corridas y no del sistema.

    La carga del Detector vacía su historial y la de Identidad reemplaza cada
    sesión, devolviéndola al estado activo.

    Args:
        url_base_datos_deteccion: Base de datos que atiende el Detector en
            marcha. Debe indicarse cuando no es la predeterminada: sembrar otra
            dejaría al Detector con la línea base de una corrida anterior y las
            mediciones de frontera partirían de un punto equivocado.
    """
    _sembrar_detector(cantidad, semilla, url_base_datos_deteccion, servicio_compose)

    credenciales = sembrar(url_identidad, cantidad=cantidad, semilla=semilla, ttl_segundos=ttl)
    verificar(url_identidad, credenciales, semilla=semilla)

    if url_detector:
        verificar_linea_base(url_detector, credenciales)

        # La comprobación deja movida la referencia de las sesiones que examinó,
        # así que se vuelve a sembrar para que la medición arranque limpia.
        _sembrar_detector(cantidad, semilla, url_base_datos_deteccion, servicio_compose)

    return guardar_credenciales(credenciales, destino)


def _sembrar_detector(
    cantidad: int,
    semilla: int,
    url_base_datos_deteccion: str | None,
    servicio_compose: str | None,
) -> None:
    """Carga el dataset semilla en la base que el Detector está atendiendo."""
    entorno = os.environ.copy()

    if url_base_datos_deteccion:
        entorno["URL_BASE_DATOS_DETECCION"] = url_base_datos_deteccion

    argumentos = ["-m", "deteccion.semilla", "--cantidad", str(cantidad),
                  "--semilla", str(semilla)]

    if servicio_compose:
        # Con Docker, la base del Detector vive en un volumen del contenedor y
        # un proceso del host no la alcanza: sembrar aquí dejaría al servicio
        # con la referencia de la corrida anterior.
        comando = ["docker", "compose", "exec", "-T", servicio_compose, "python", *argumentos]
    else:
        comando = [sys.executable, *argumentos]

    resultado = subprocess.run(comando, cwd=RAIZ, capture_output=True, text=True, env=entorno)

    if resultado.returncode != 0:
        raise RuntimeError(f"No se pudo sembrar el Detector: {resultado.stderr.strip()}")


def verificar_linea_base(url_detector: str, credenciales, muestras: int = 5) -> None:
    """
    Comprueba que el Detector parte de la ubicación recién sembrada.

    Se consulta al propio Detector en lugar de confiar en que la siembra llegó a
    su base de datos: cuando el servicio corre en un contenedor, su base vive en
    un volumen que el proceso de siembra no alcanza, y la referencia conserva el
    arrastre de la corrida anterior. Las distancias de la barrida se medirían
    entonces desde otro punto y las suspensiones parecerían falsos positivos del
    sistema.

    La comprobación se hace a un paso apenas dentro del radio, no en el punto
    exacto: una referencia desplazada unos cientos de metros sigue aceptando su
    propia ubicación, pero delata el desvío justo en el borde, que es donde la
    medición de frontera se juega.

    Cada sesión se prueba una sola vez, alternando el sentido del
    desplazamiento entre sesiones. Probar los dos sentidos sobre la misma sesión
    no funciona: la primera petición es aceptada y reescribe la referencia, de
    modo que la segunda se mide desde el punto que acaba de fijar la primera y
    sale sospechosa aunque el estado fuera correcto. Alternar entre sesiones
    cubre igualmente el punto ciego de un arrastre colineal, porque basta con
    que una de las sesiones se pruebe en sentido contrario a la deriva.

    Se usan las últimas sesiones de la población porque la barrida de frontera
    reparte las distancias desde el principio: así las sesiones que esta
    comprobación desplaza no son las que luego se miden.

    Raises:
        RuntimeError: Si la referencia del Detector no es la sembrada.
    """
    import requests

    from parametros import RADIO_UBICACION_KM

    from .poblacion import desplazar

    # Un punto que, desde la ubicación sembrada, queda claramente dentro del
    # radio. Si el Detector lo rechaza, su referencia no es la sembrada.
    distancia_prueba = RADIO_UBICACION_KM * 0.98

    for indice, credencial in enumerate(credenciales[-muestras:]):
        sentido = 1 if indice % 2 == 0 else -1
        lat, lon = desplazar(credencial.lat, credencial.lon, sentido * distancia_prueba)

        respuesta = requests.post(
            f"{url_detector.rstrip('/')}/evaluar",
            json={
                "session_id": credencial.session_id,
                "device_id": credencial.device_id,
                "geo_lat": lat,
                "geo_lon": lon,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            timeout=5,
        )

        if respuesta.status_code != 200:
            raise RuntimeError(
                f"El Detector no respondió a la comprobación de línea base "
                f"(HTTP {respuesta.status_code})."
            )

        veredicto = respuesta.json()

        if veredicto.get("veredicto") != "normal":
            raise RuntimeError(
                f"El Detector marca sospechosa a {credencial.session_id} a "
                f"{distancia_prueba:g} km de su ubicación sembrada, dentro del "
                f"radio de {RADIO_UBICACION_KM:g} km "
                f"(motivo={veredicto.get('motivo')}). Su base de datos no es la "
                f"que se sembró: con Docker, pasa --servicio-compose detector "
                f"para sembrar dentro del contenedor; en local, indica "
                f"--url-base-datos-deteccion."
            )


def ejecutar_locust(
    *,
    host: str,
    escenario: str,
    ejecucion_id: str,
    usuarios: int,
    spawn_rate: float,
    duracion: str,
    credenciales: Path,
    destino_registro: Path | None,
    prefijo_csv: Path | None,
) -> None:
    """
    Corre Locust en modo headless y espera a que termine.

    Si no se indica destino del registro la corrida es descartable: sirve para
    calentar las conexiones sin contaminar la evidencia.
    """
    entorno = os.environ.copy()
    entorno.update({
        "ESCENARIO": escenario,
        "EJECUCION_ID": ejecucion_id,
        "CAMINO_CREDENCIALES": str(credenciales),
        "USUARIOS_CONFIGURADOS": str(usuarios),
        "DESTINO_REGISTRO": str(destino_registro) if destino_registro else "",
        "PYTHONUNBUFFERED": "1",
    })

    comando = [
        sys.executable, "-m", "locust",
        "-f", str(LOCUSTFILE),
        "--headless",
        "--host", host,
        "-u", str(usuarios),
        "-r", str(spawn_rate),
        "-t", duracion,
        # Una suspensión es el desenlace correcto ante una suplantación, y el
        # locustfile ya marca cada petición según coincida con su etiqueta. Sin
        # este indicador, un desacuerdo legítimo del experimento tumbaría el
        # proceso como si fuera un error del arnés.
        "--exit-code-on-error", "0",
    ]

    if prefijo_csv is not None:
        prefijo_csv.parent.mkdir(parents=True, exist_ok=True)
        comando += ["--csv", str(prefijo_csv), "--csv-full-history"]
    else:
        comando += ["--only-summary"]

    subprocess.run(comando, check=True, cwd=RAIZ, env=entorno)


def verificar_integridad(prefijo_csv: Path, registro: Path) -> None:
    """
    Comprueba que la corrida dejó evidencia utilizable.

    Raises:
        RuntimeError: Si falta el resumen de Locust, no hubo tráfico o el
            registro por petición quedó vacío.
    """
    resumen = Path(f"{prefijo_csv}_stats.csv")

    if not resumen.is_file():
        raise RuntimeError(f"No se generó {resumen.name}")

    with resumen.open(encoding="utf-8") as archivo:
        filas = {fila["Name"]: fila for fila in csv.DictReader(archivo)}

    agregada = filas.get("Aggregated")

    if agregada is None:
        raise RuntimeError(f"{resumen.name} no tiene fila 'Aggregated'")

    if int(agregada["Request Count"]) == 0:
        raise RuntimeError(f"{resumen.name} quedó sin peticiones: la corrida no generó tráfico")

    if not registro.is_file() or registro.stat().st_size == 0:
        raise RuntimeError(
            f"{registro} quedó vacío: sin él no hay verdad-terreno y no se "
            f"puede calcular la matriz de confusión"
        )


def correr_repeticion(escenario, argumentos, indice: int) -> Path:
    """Ejecuta una repetición completa y devuelve su directorio de evidencia."""
    corrida_id = f"{argumentos.ejecucion_id}_rep{indice}"
    directorio = Path(argumentos.resultados_dir) / f"escenario_{escenario.letra}" / corrida_id
    duracion_s = segundos_desde_duracion(escenario.duracion)

    if duracion_s > DURACION_MAXIMA_S:
        raise SystemExit(
            f"La duración {escenario.duracion} supera los {DURACION_MAXIMA_S}s: "
            f"la ventana de actividad del Detector dejaría de cubrir a los "
            f"clientes sembrados y las suplantaciones por ubicación se contarían "
            f"como falsos negativos del experimento."
        )

    print(f"\n=== {descripcion(escenario.letra)}")
    print(f"    corrida {corrida_id} — {escenario.usuarios} usuarios, {escenario.duracion}")

    # 1. Estado de partida idéntico para toda repetición.
    print("    resembrando Detector e Identidad...")
    credenciales = resembrar(
        argumentos.url_identidad,
        argumentos.cantidad,
        argumentos.semilla,
        argumentos.ttl,
        Path(argumentos.credenciales),
        argumentos.url_base_datos_deteccion,
        argumentos.url_detector,
        argumentos.servicio_compose,
    )

    # 2. Calentamiento descartable: la primera petición paga la apertura de
    #    conexiones y las tablas aún frías, y ese costo no es el que se mide.
    #
    #    Se calienta siempre con el escenario de suplantación por dispositivo,
    #    nunca con el del escenario medido. Una petición aceptada reescribe la
    #    última ubicación conocida, de modo que calentar con tráfico que se
    #    desplaza arrastraría la línea base y la medición arrancaría desde un
    #    punto que no es el sembrado: se comprobó que así una sesión a 49 km del
    #    origen podía quedar a más de 50 km de su referencia y contarse como
    #    falso positivo. El tráfico por dispositivo se rechaza siempre, así que
    #    calienta las conexiones sin tocar la geografía.
    if not argumentos.sin_calentamiento:
        print("    calentando...")
        ejecutar_locust(
            host=argumentos.host, escenario=ESCENARIO_CALENTAMIENTO,
            ejecucion_id=f"{corrida_id}_calent",
            usuarios=2, spawn_rate=2, duracion="5s", credenciales=credenciales,
            destino_registro=None, prefijo_csv=None,
        )

        # El calentamiento deja sesiones en `pendiente_verificacion`, así que se
        # restaura el estado de partida antes de medir.
        credenciales = resembrar(
            argumentos.url_identidad, argumentos.cantidad, argumentos.semilla,
            argumentos.ttl, Path(argumentos.credenciales),
            argumentos.url_base_datos_deteccion, argumentos.url_detector,
            argumentos.servicio_compose,
        )

    # 3. El manifest se escribe antes de medir: si la corrida se interrumpe,
    #    las condiciones de lo que alcanzó a medirse quedan registradas.
    manifest_mod.guardar(
        manifest_mod.construir(
            corrida_id, escenario.letra,
            usuarios=escenario.usuarios,
            duracion_segundos=duracion_s,
            spawn_rate=escenario.spawn_rate,
            tamano_dataset=argumentos.cantidad,
            semilla_aleatoria=argumentos.semilla,
            ttl_sesion_segundos=argumentos.ttl,
            distancias_frontera=escenario.distancias_frontera,
            detector=argumentos.detector,
        ),
        directorio,
    )

    # 4. Corrida medida.
    registro = directorio / NOMBRE_REGISTRO
    prefijo_csv = directorio / "results"

    ejecutar_locust(
        host=argumentos.host, escenario=escenario.letra, ejecucion_id=corrida_id,
        usuarios=escenario.usuarios, spawn_rate=escenario.spawn_rate,
        duracion=escenario.duracion, credenciales=credenciales,
        destino_registro=registro, prefijo_csv=prefijo_csv,
    )

    verificar_integridad(prefijo_csv, registro)
    print(f"    evidencia en {directorio}")

    return directorio


def parsear(argv=None):
    """Define y analiza los argumentos del orquestador."""
    analizador = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    analizador.add_argument("--escenario", default="TODOS",
                            help="Letra, lista separada por comas, o TODOS.")
    analizador.add_argument("--repeticiones", type=int, default=1)
    analizador.add_argument("--ejecucion-id", default="local")
    analizador.add_argument("--host", default="http://127.0.0.1:8080",
                            help="URL base del Gateway.")
    analizador.add_argument("--url-identidad", default="http://127.0.0.1:8002")
    analizador.add_argument("--url-detector", default="http://127.0.0.1:8001",
                            help="URL del Detector, para comprobar su línea base.")
    analizador.add_argument(
        "--servicio-compose", default=None,
        help="Nombre del servicio de Compose que sirve al Detector (p. ej. "
             "'detector'). Con Docker su base vive en un volumen del "
             "contenedor, así que la siembra debe ejecutarse dentro.",
    )
    analizador.add_argument(
        "--url-base-datos-deteccion",
        default=os.environ.get("URL_BASE_DATOS_DETECCION"),
        help="Base de datos que atiende el Detector en marcha. Debe coincidir "
             "con la suya: sembrar otra lo dejaría con la línea base de una "
             "corrida anterior.",
    )
    analizador.add_argument("--resultados-dir", default=str(RAIZ / "resultados"))
    analizador.add_argument("--credenciales", default=str(CAMINO_CREDENCIALES_POR_DEFECTO))
    analizador.add_argument("--cantidad", type=int, default=TAMANO_DATASET_SEMILLA)
    analizador.add_argument("--semilla", type=int, default=SEMILLA_ALEATORIA)
    analizador.add_argument("--ttl", type=int, default=TTL_SESION_SEGUNDOS)
    analizador.add_argument("--usuarios", type=int, default=None,
                            help="Sobreescribe los usuarios de cada escenario.")
    analizador.add_argument("--duracion", default=None,
                            help="Sobreescribe la duración de cada escenario.")
    analizador.add_argument("--detector", choices=(manifest_mod.DETECTOR_REAL,
                                                   manifest_mod.DETECTOR_DOBLE),
                            default=manifest_mod.DETECTOR_REAL,
                            help="Qué Detector está sirviendo; queda en el manifest.")
    analizador.add_argument("--sin-calentamiento", action="store_true")
    analizador.add_argument("--continuar-si-falla", action="store_true",
                            help="Sigue con el resto de la suite si una repetición falla.")

    return analizador.parse_args(argv)


def main(argv=None) -> int:
    """Corre la suite solicitada."""
    argumentos = parsear(argv)
    letras = resolver(argumentos.escenario)
    fallidas = []

    for letra in letras:
        # Los ajustes se aplican sobre una copia: mutar el registro global haría
        # que el ajuste de un escenario se filtrara a los siguientes.
        escenario = ESCENARIOS[letra].con_ajustes(
            usuarios=argumentos.usuarios,
            duracion=argumentos.duracion,
        )

        for indice in range(1, argumentos.repeticiones + 1):
            try:
                correr_repeticion(escenario, argumentos, indice)
            except (RuntimeError, ErrorSiembra, manifest_mod.ErrorManifest,
                    subprocess.CalledProcessError) as error:
                fallidas.append(f"{letra}_rep{indice}: {error}")
                print(f"    FALLÓ: {error}", file=sys.stderr)

                if not argumentos.continuar_si_falla:
                    return 1

    if fallidas:
        print(f"\n{len(fallidas)} repetición(es) fallida(s):", file=sys.stderr)
        for fallo in fallidas:
            print(f"  - {fallo}", file=sys.stderr)
        return 1

    print(f"\nSuite completa. Evidencia en {argumentos.resultados_dir}")
    print("Consolida con: python -m analisis.main")

    return 0


if __name__ == "__main__":
    sys.exit(main())
