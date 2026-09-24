"""
Parámetros del experimento de seguridad (HA16), compartidos por los cuatro
componentes: Gateway, ms-identidad-sesiones, ms-deteccion-sesiones y el
generador de tráfico.

Fijar estos valores en un único lugar es lo que permite que la evidencia
recogida por cada integrante sea comparable: si el Detector midiera contra un
sub-presupuesto y el generador de tráfico contra otro, los resultados no se
podrían consolidar.

Todos los valores admiten sobreescritura por variable de entorno para poder
explorar sensibilidad sin tocar el código.
"""

import os


def _entero(nombre: str, predeterminado: int) -> int:
    """Lee un parámetro entero del entorno, o devuelve el valor acordado."""
    return int(os.environ.get(nombre, predeterminado))


def _decimal(nombre: str, predeterminado: float) -> float:
    """Lee un parámetro decimal del entorno, o devuelve el valor acordado."""
    return float(os.environ.get(nombre, predeterminado))


# Presupuesto total de la meta HA16: detectar y suspender la operación
# sospechosa en menos de un segundo desde que llega la solicitud.
UMBRAL_LATENCIA_MS = _entero("UMBRAL_LATENCIA_MS", 1000)

# Sub-presupuesto asignado únicamente al Detector, dejando margen al resto
# del flujo (validación en el Gateway, consulta a Identidad y reenvío).
UMBRAL_LATENCIA_DETECCION_MS = _entero("UMBRAL_LATENCIA_DETECCION_MS", 200)

# Distancia máxima aceptable entre la ubicación de la solicitud y la última
# ubicación conocida del cliente antes de marcar "ubicación incompatible".
RADIO_UBICACION_KM = _decimal("RADIO_UBICACION_KM", 50.0)

# Cuánto tiempo atrás se considera "actividad reciente". Más allá de esta
# ventana, la última ubicación conocida deja de ser comparable: el cliente
# pudo desplazarse legítimamente y la regla de ubicación no debe aplicarse.
VENTANA_ACTIVIDAD_RECIENTE_MIN = _entero("VENTANA_ACTIVIDAD_RECIENTE_MIN", 60)

# Proporción de peticiones que el generador de tráfico produce como
# suplantadas frente a legítimas (0.8 equivale al reparto 80/20 acordado).
TASA_TRAFICO_SUPLANTADO = _decimal("TASA_TRAFICO_SUPLANTADO", 0.8)

# Cuántos clientes con dispositivo y ubicación registrados se precargan en
# SQLite para que el Detector tenga contra qué comparar.
TAMANO_DATASET_SEMILLA = _entero("TAMANO_DATASET_SEMILLA", 200)

# Semilla del generador pseudoaleatorio del dataset. Fijarla es lo que hace
# que la población de datos sea reproducible entre corridas y entre máquinas.
SEMILLA_ALEATORIA = _entero("SEMILLA_ALEATORIA", 20260923)
