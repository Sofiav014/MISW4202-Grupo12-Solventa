"""API para recibir, validar, vincular y guardar evidencia de una ejecución."""

from .contratos import (
    AdjuntoCSV, AlmacenResultados, EntradaManifest, ErrorEntradaResultados,
    ErrorIntegridadResultados, ErrorPersistenciaResultados, ErrorResultados,
    EvidenciaCorrida, PaqueteExistenteError, ProcedenciaRegistros, TablaRegistros,
)
from .entradas import leer_adjunto, leer_manifest, leer_registros
from .persistencia import AlmacenResultadosLocal, validar_paquete
from .log_compartido import leer_log_compartido, preparar_corridas_desde_log
from .servicio import guardar_resultados

__all__ = [
    "AdjuntoCSV", "AlmacenResultados", "AlmacenResultadosLocal", "EntradaManifest",
    "ErrorEntradaResultados", "ErrorIntegridadResultados", "ErrorPersistenciaResultados",
    "ErrorResultados", "EvidenciaCorrida", "PaqueteExistenteError", "ProcedenciaRegistros", "TablaRegistros",
    "guardar_resultados", "leer_adjunto", "leer_manifest", "leer_registros", "validar_paquete",
    "leer_log_compartido", "preparar_corridas_desde_log",
]
