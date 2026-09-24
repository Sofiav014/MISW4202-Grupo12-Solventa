from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, String
from sqlalchemy.orm import declarative_base


Base = declarative_base()

VEREDICTO_NORMAL = "normal"
VEREDICTO_SOSPECHOSO = "sospechoso"

MOTIVO_DISPOSITIVO = "dispositivo"
MOTIVO_UBICACION = "ubicacion"


class ClienteConocido(Base):
    """
    Historial que el Detector mantiene por sesión para poder comparar.

    La última ubicación conocida vive aquí y no en ms-identidad-sesiones: ese
    servicio administra el ciclo de vida de la sesión, no la geografía del
    cliente. Guardarla junto al instante en que se observó es lo que permite
    aplicar la regla de ubicación solo cuando el dato sigue siendo reciente.
    """

    __tablename__ = "clientes_conocidos"

    session_id = Column(String, primary_key=True)
    cliente_id = Column(String, nullable=False)
    device_id_registrado = Column(String, nullable=False)
    ultima_lat = Column(Float, nullable=True)
    ultima_lon = Column(Float, nullable=True)
    ultima_actividad = Column(DateTime, nullable=True)


@dataclass(frozen=True)
class ContextoEvaluacion:
    """Contexto de sesión recibido del Gateway, ya validado."""

    session_id: str
    device_id: str
    geo_lat: float
    geo_lon: float
    instante: datetime


@dataclass(frozen=True)
class LineaBase:
    """
    Referencia contra la que se contrasta una solicitud.

    Combina lo que aporta ms-identidad-sesiones (dispositivo registrado) con
    lo que el Detector observó por su cuenta (última ubicación conocida).

    Attributes:
        device_id_registrado:
            Dispositivo asociado al cliente, o ``None`` si no hay ninguno.

        ultima_lat, ultima_lon:
            Última ubicación observada, si existe.

        ultima_actividad:
            Instante de esa última observación.

        identidad_disponible:
            Indica si ms-identidad-sesiones respondió. En modo degradado la
            línea base proviene únicamente del registro local.
    """

    device_id_registrado: str | None
    ultima_lat: float | None = None
    ultima_lon: float | None = None
    ultima_actividad: datetime | None = None
    identidad_disponible: bool = True


@dataclass(frozen=True)
class Veredicto:
    """
    Decisión del Detector sobre una solicitud.

    Los tres campos son exactamente los que el Gateway valida de forma
    estricta; añadir cualquier otro haría que descartara la respuesta.
    """

    veredicto: str
    motivo: str | None
    score_riesgo: float

    def a_dict(self) -> dict:
        """Serializa el veredicto al contrato acordado con el Gateway."""
        return {
            "veredicto": self.veredicto,
            "motivo": self.motivo,
            "score_riesgo": self.score_riesgo,
        }
