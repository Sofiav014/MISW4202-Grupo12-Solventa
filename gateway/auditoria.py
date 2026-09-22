import json
import sys


class RegistradorAuditoriaJson:
    def __init__(self, salida=None):
        self.salida = salida or sys.stdout

    def registrar(self, evento, **campos):
        registro = {"evento": evento, **campos}
        self.salida.write(json.dumps(registro, separators=(",", ":"), sort_keys=True) + "\n")
        self.salida.flush()
