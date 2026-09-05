"""Contratos de entrada y almacenamiento de evidencia de la actividad 5.4."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, TypeAlias

from experimentos.manifest import ManifestCorrida

ValorJSON: TypeAlias = (
    str | int | float | bool | None | list["ValorJSON"] | dict[str, "ValorJSON"]
)
CAMPOS_TRAZABILIDAD = ("request_id", "ejecucion_id", "escenario")
_PERCENTILES = ("50%", "66%", "75%", "80%", "90%", "95%", "98%", "99%", "99.9%", "99.99%", "100%")
COLUMNAS_ADJUNTOS: Mapping[str, tuple[str, ...]] = {
    "results_stats.csv": (
        "Type", "Name", "Request Count", "Failure Count", "Median Response Time",
        "Average Response Time", "Min Response Time", "Max Response Time",
        "Average Content Size", "Requests/s", "Failures/s", *_PERCENTILES,
    ),
    "results_stats_history.csv": (
        "Timestamp", "User Count", "Type", "Name", "Requests/s", "Failures/s",
        *_PERCENTILES, "Total Request Count", "Total Failure Count",
        "Total Median Response Time", "Total Average Response Time",
        "Total Min Response Time", "Total Max Response Time", "Total Average Content Size",
    ),
    "results_failures.csv": ("Method", "Name", "Error", "Occurrences"),
    "results_exceptions.csv": ("Count", "Message", "Traceback", "Nodes"),
}


class ErrorResultados(Exception):
    """Error esperado al recibir, verificar o guardar evidencia."""


class ErrorEntradaResultados(ErrorResultados):
    """Un archivo de entrada no puede leerse o interpretarse."""


class ErrorIntegridadResultados(ErrorResultados):
    """La evidencia esta incompleta o contiene datos inconsistentes."""


class ErrorPersistenciaResultados(ErrorResultados):
    """No se pudo publicar el paquete en el filesystem."""


class PaqueteExistenteError(ErrorPersistenciaResultados):
    """El destino contiene evidencia incompatible o esta ocupado por otro guardado."""


@dataclass(frozen=True)
class EntradaManifest:
    """DTO real de 4.3 junto con sus bytes originales, que se conservaran."""

    modelo: ManifestCorrida
    contenido: bytes


@dataclass(frozen=True)
class TablaRegistros:
    """Peticiones de una corrida, con orden y campos originales."""

    columnas: tuple[str, ...]
    filas: tuple[Mapping[str, ValorJSON], ...]


@dataclass(frozen=True)
class AdjuntoCSV:
    """CSV agregado externo; su nombre debe pertenecer al contrato conocido."""

    nombre: str
    contenido: bytes


@dataclass(frozen=True)
class ProcedenciaRegistros:
    """Origen y alcance de la seleccion realizada por un adaptador de entrada."""

    archivo_fuente: str
    sha256_fuente: str
    criterio_seleccion: str
    ventana_medicion_confirmada: bool
    limitaciones: tuple[str, ...]


@dataclass(frozen=True)
class EvidenciaCorrida:
    """Entradas de una corrida terminada; no describe como ejecutarla."""

    manifest: EntradaManifest
    registros: TablaRegistros
    adjuntos: tuple[AdjuntoCSV, ...] = ()
    procedencia: ProcedenciaRegistros | None = None


class AlmacenResultados(Protocol):
    """Borde sustituible para persistir evidencia validada por el servicio."""

    def guardar(self, evidencia: EvidenciaCorrida) -> Path:
        """Publica la evidencia y devuelve la ubicacion del paquete."""
        ...
