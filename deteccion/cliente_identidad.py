from dataclasses import dataclass
from datetime import datetime
import json
import time

import requests


@dataclass(frozen=True)
class DatosSesion:
    """Respuesta útil de IValidarSesión, ya convertida a tipos internos."""

    device_id_registrado: str | None
    ultima_actividad: datetime | None
    sesion_activa: bool
    estado: str


@dataclass(frozen=True)
class FalloIdentidad:
    """Describe por qué no se pudo obtener la información de la sesión."""

    categoria: str
    latencia_ms: float


class ClienteIdentidad:
    """
    Consume IValidarSesión de ms-identidad-sesiones.

    El Detector necesita de Identidad el dispositivo registrado y el estado de
    la sesión. La ubicación no forma parte de este contrato: la administra el
    propio Detector, que es quien observa cada solicitud.
    """

    def __init__(self, url: str, tiempo_espera_ms: int = 120, transporte=None):
        """
        Args:
            url:
                URL base de ms-identidad-sesiones.

            tiempo_espera_ms:
                Espera máxima. Se mantiene por debajo del sub-presupuesto del
                Detector para que una Identidad lenta no consuma por sí sola
                todo el margen de latencia asignado a la detección.

            transporte:
                Transporte alternativo, utilizado por las pruebas para
                sustituir la llamada HTTP real.
        """
        self.url = url.rstrip("/")
        self.tiempo_espera_ms = tiempo_espera_ms
        self.transporte = transporte

    def validar_sesion(self, session_id: str) -> DatosSesion | FalloIdentidad:
        """
        Consulta el dispositivo registrado y el estado de una sesión.

        Returns:
            ``DatosSesion`` cuando Identidad responde conforme al contrato.

            ``FalloIdentidad`` ante timeout, error de conexión o una respuesta
            que no cumple el contrato. El llamador decide cómo degradar.
        """
        inicio = time.monotonic()

        try:
            if self.transporte is not None:
                estado, cuerpo = self.transporte(session_id)
            else:
                respuesta = requests.post(
                    self.url + "/sesiones/validar",
                    json={"session_id": session_id},
                    timeout=self.tiempo_espera_ms / 1000,
                )
                estado, cuerpo = respuesta.status_code, respuesta.content

            transcurrido = (time.monotonic() - inicio) * 1000

            if estado != 200:
                return FalloIdentidad("http_status", transcurrido)

            datos = json.loads(cuerpo)

            if not isinstance(datos, dict):
                return FalloIdentidad("contrato_invalido", transcurrido)

            return DatosSesion(
                device_id_registrado=datos.get("device_id_registrado"),
                ultima_actividad=self._a_instante(datos.get("ultima_actividad")),
                sesion_activa=bool(datos.get("sesion_activa")),
                estado=str(datos.get("estado", "desconocida")),
            )

        except requests.Timeout:
            return FalloIdentidad("tiempo_espera", (time.monotonic() - inicio) * 1000)
        except requests.RequestException:
            return FalloIdentidad("conexion", (time.monotonic() - inicio) * 1000)
        except (ValueError, TypeError, json.JSONDecodeError):
            return FalloIdentidad("contrato_invalido", (time.monotonic() - inicio) * 1000)

    @staticmethod
    def _a_instante(valor) -> datetime | None:
        """Convierte el instante ISO 8601 de la respuesta, tolerando ausencia o formato inesperado."""
        if not isinstance(valor, str) or not valor:
            return None

        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            return None
