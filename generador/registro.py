"""
Registro por petición del experimento: la fuente de verdad del análisis.

El generador anota cada petición junto con lo que debería haberle pasado. Sin
esa etiqueta no hay matriz de confusión posible, porque el código de respuesta
no distingue un 403 acertado sobre un ataque de un 403 equivocado sobre un
cliente legítimo.

El formato es JSONL —un objeto por línea— y el esquema está congelado: el
lector del análisis rechaza cualquier archivo que declare otra versión, para
que un cambio de columnas no se descubra a mitad de la consolidación.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import threading

# Versión del esquema del registro. Se incrementa si cambian las columnas; el
# análisis se niega a leer una versión que no conoce.
ESQUEMA_VERSION = 1

# Columnas del registro, en el orden en que se documentan. El análisis valida
# contra esta tupla, así que es el contrato entre ambos paquetes.
COLUMNAS = (
    "request_id",
    "ejecucion_id",
    "escenario",
    "modo",
    "etiqueta_verdad",
    "session_id",
    "device_alterado",
    "distancia_km",
    "ts_envio",
    "ts_respuesta",
    "latencia_ms",
    "status",
    "error_code",
    "veredicto_observado",
    "fallo_transporte",
    "usuarios_configurados",
    "esquema_version",
)

# Veredictos observables desde el lado del cliente.
OBSERVADO_SUSPENDIDA = "suspendida"
OBSERVADO_ENRUTADA = "enrutada"
OBSERVADO_RECHAZADA = "rechazada"
OBSERVADO_ERROR_INFRA = "error_infra"

NOMBRE_ARCHIVO = "peticiones.jsonl"


def clasificar(status: int | None, error_code: str | None) -> str:
    """
    Traduce la respuesta del Gateway a un veredicto observable.

    Se distinguen cuatro desenlaces porque solo dos de ellos son decisiones de
    clasificación. Un 400 o un 401 dicen que la petición estaba mal formada o
    mal autenticada, y un 5xx que una dependencia falló: ninguno es un juicio
    del Detector sobre la sesión, así que el análisis los excluye del
    denominador de la matriz en lugar de contarlos como aciertos o errores.

    Args:
        status: Código HTTP recibido, o `None` si no hubo respuesta.
        error_code: Código de error del cuerpo, cuando el Gateway lo envía.

    Returns:
        Uno de los `OBSERVADO_*`.
    """
    if status is None:
        return OBSERVADO_ERROR_INFRA

    # La suspensión es el desenlace que HA16 persigue: el Gateway detuvo la
    # operación y pidió verificación reforzada antes de tocar el journey.
    if status == 403 and error_code == "verification_required":
        return OBSERVADO_SUSPENDIDA

    if 200 <= status < 300:
        return OBSERVADO_ENRUTADA

    if status in (400, 401, 403):
        return OBSERVADO_RECHAZADA

    return OBSERVADO_ERROR_INFRA


@dataclass(frozen=True)
class Anotacion:
    """Una fila del registro, ya resuelta a los valores que se persisten."""

    request_id: str
    ejecucion_id: str
    escenario: str
    modo: str
    etiqueta_verdad: str
    session_id: str
    device_alterado: bool
    distancia_km: float | None
    ts_envio: float
    ts_respuesta: float
    latencia_ms: float
    status: int | None
    error_code: str | None
    veredicto_observado: str
    fallo_transporte: str | None
    usuarios_configurados: int

    def a_dict(self) -> dict:
        """Proyecta la anotación al objeto JSON que se escribe en el registro."""
        return {
            "request_id": self.request_id,
            "ejecucion_id": self.ejecucion_id,
            "escenario": self.escenario,
            "modo": self.modo,
            "etiqueta_verdad": self.etiqueta_verdad,
            "session_id": self.session_id,
            "device_alterado": self.device_alterado,
            "distancia_km": self.distancia_km,
            "ts_envio": self.ts_envio,
            "ts_respuesta": self.ts_respuesta,
            "latencia_ms": self.latencia_ms,
            "status": self.status,
            "error_code": self.error_code,
            "veredicto_observado": self.veredicto_observado,
            "fallo_transporte": self.fallo_transporte,
            "usuarios_configurados": self.usuarios_configurados,
            "esquema_version": ESQUEMA_VERSION,
        }


class RegistroPeticiones:
    """
    Escribe el registro de una corrida, una línea por petición.

    Locust ejecuta sus usuarios como greenlets sobre un mismo hilo, pero el
    orquestador puede lanzar varios procesos. La escritura se serializa con un
    cerrojo y cada línea se vuelca de inmediato, de modo que una corrida
    interrumpida conserve todo lo medido hasta ese punto en lugar de perder el
    contenido que quedara en memoria.
    """

    def __init__(self, destino: Path):
        """
        Args:
            destino: Archivo JSONL a escribir; sus directorios se crean si faltan.
        """
        self.destino = Path(destino)
        self.destino.parent.mkdir(parents=True, exist_ok=True)
        self._cerrojo = threading.Lock()
        self._archivo = self.destino.open("a", encoding="utf-8")

    def anotar(self, anotacion: Anotacion) -> None:
        """Persiste una petición."""
        linea = json.dumps(anotacion.a_dict(), separators=(",", ":"), sort_keys=True)

        with self._cerrojo:
            self._archivo.write(linea + "\n")
            self._archivo.flush()

    def cerrar(self) -> None:
        """Cierra el archivo del registro."""
        with self._cerrojo:
            if not self._archivo.closed:
                self._archivo.close()

    def __enter__(self) -> "RegistroPeticiones":
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()
