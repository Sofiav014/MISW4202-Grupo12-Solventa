from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()

ESTADO_ACTIVA = "activa"
ESTADO_PENDIENTE_VERIFICACION = "pendiente_verificacion"
ESTADO_REVOCADA = "revocada"
ESTADO_EXPIRADA = "expirada"


class Sesion(Base):
    """Registro persistido en SQLite del estado de una sesión."""

    __tablename__ = "sesiones"

    session_id = Column(String, primary_key=True)
    device_id_registrado = Column(String, nullable=True)
    estado = Column(String, nullable=False, default=ESTADO_ACTIVA)
    creada_en = Column(DateTime, nullable=False)
    ultima_actividad = Column(DateTime, nullable=False)
    expira_en = Column(DateTime, nullable=True)
    referencia_verificacion = Column(String, nullable=True)


@dataclass(frozen=True)
class InstantaneaSesion:
    """Representación inmutable del estado de una sesión en un instante dado."""

    session_id: str
    device_id_registrado: str | None
    estado: str
    ultima_actividad: datetime | None
    expira_en: datetime | None
    referencia_verificacion: str | None = None


@dataclass(frozen=True)
class ResultadoVerificacion:
    """Resultado de iniciar una verificación reforzada para una sesión."""

    referencia: str
    ya_estaba_pendiente: bool
