import json
import sys


class RegistradorAuditoriaJson:
    """
    Emite los eventos del Detector como JSON por línea.

    Cada evaluación deja registrada su latencia y si quedó dentro del
    sub-presupuesto; esa traza es el insumo con el que se documenta el tiempo
    de respuesta exigido por los criterios del experimento.
    """

    def __init__(self, salida=None):
        """Escribe en `salida`, o en la salida estándar si no se indica otra."""
        self.salida = salida or sys.stdout

    def registrar(self, evento, **campos):
        """Serializa un evento con sus campos en una única línea."""
        registro = {"evento": evento, **campos}
        self.salida.write(json.dumps(registro, separators=(",", ":"), sort_keys=True) + "\n")
        self.salida.flush()
