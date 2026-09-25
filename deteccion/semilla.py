"""
Carga del dataset semilla de ms-deteccion-sesiones.

Precarga clientes con dispositivo y ubicación conocidos para que el Detector
tenga contra qué comparar desde la primera petición. La población se genera a
partir de una semilla fija, de modo que dos ejecuciones —en cualquier máquina—
producen exactamente el mismo conjunto de datos y las corridas del experimento
son reproducibles.
"""

import argparse
from datetime import datetime, timedelta, timezone
import random

from parametros import SEMILLA_ALEATORIA, TAMANO_DATASET_SEMILLA
from .configuracion import ConfiguracionDeteccion
from .persistencia import AlmacenClientes
from .repositorio import RepositorioClientes


# Ciudades de referencia con sus coordenadas. Estar suficientemente separadas
# entre sí es lo que permite al generador de tráfico construir un escenario de
# ubicación incompatible sin ambigüedad.
CIUDADES = [
    ("bogota", 4.7110, -74.0721),
    ("medellin", 6.2442, -75.5812),
    ("cali", 3.4516, -76.5320),
    ("barranquilla", 10.9685, -74.7813),
    ("bucaramanga", 7.1193, -73.1227),
    ("cartagena", 10.3910, -75.4794),
]


def generar(cantidad: int, semilla: int) -> list[dict]:
    """
    Construye la población de clientes de forma determinista.

    Args:
        cantidad: Número de clientes a generar.
        semilla: Semilla del generador pseudoaleatorio.

    Returns:
        Lista de diccionarios con sesión, cliente, dispositivo y ubicación.
    """
    aleatorio = random.Random(semilla)
    ahora = datetime.now(timezone.utc)
    poblacion = []

    for indice in range(cantidad):
        ciudad, lat, lon = CIUDADES[indice % len(CIUDADES)]

        # Dispersión de pocos kilómetros alrededor del centro de la ciudad,
        # muy por debajo del radio permitido: el ruido no debe por sí mismo
        # disparar la regla de ubicación.
        lat_cliente = lat + aleatorio.uniform(-0.05, 0.05)
        lon_cliente = lon + aleatorio.uniform(-0.05, 0.05)

        # Actividad reciente escalonada dentro de la última media hora, para
        # que la ventana de comparación esté vigente al arrancar la corrida.
        minutos = aleatorio.randint(0, 30)

        poblacion.append({
            "session_id": f"sesion-{indice:05d}",
            "cliente_id": f"cliente-{indice:05d}",
            "device_id": f"dispositivo-{ciudad}-{indice:05d}",
            "lat": round(lat_cliente, 6),
            "lon": round(lon_cliente, 6),
            "instante": ahora - timedelta(minutes=minutos),
        })

    return poblacion


def cargar(configuracion: ConfiguracionDeteccion, cantidad: int, semilla: int) -> int:
    """
    Vacía el historial y carga la población semilla.

    Returns:
        Número de clientes registrados.
    """
    repositorio = RepositorioClientes(AlmacenClientes(configuracion.url_base_datos))
    repositorio.vaciar()

    for cliente in generar(cantidad, semilla):
        repositorio.registrar(
            cliente["session_id"],
            cliente["cliente_id"],
            cliente["device_id"],
            cliente["lat"],
            cliente["lon"],
            cliente["instante"],
        )

    return repositorio.contar()


def main() -> None:
    """Punto de entrada del script de carga."""
    analizador = argparse.ArgumentParser(description="Carga el dataset semilla del Detector.")
    analizador.add_argument("--cantidad", type=int, default=TAMANO_DATASET_SEMILLA)
    analizador.add_argument("--semilla", type=int, default=SEMILLA_ALEATORIA)
    argumentos = analizador.parse_args()

    configuracion = ConfiguracionDeteccion.desde_entorno()
    total = cargar(configuracion, argumentos.cantidad, argumentos.semilla)

    print(
        f"Dataset semilla cargado: {total} clientes "
        f"(semilla={argumentos.semilla}, destino={configuracion.url_base_datos})"
    )


if __name__ == "__main__":
    main()
