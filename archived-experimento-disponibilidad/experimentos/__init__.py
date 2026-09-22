"""Contratos reutilizables para registrar evidencia de los experimentos."""

from .manifest import (
    ConfiguracionCache,
    ConfiguracionCarga,
    ConfiguracionCircuitBreaker,
    ConfiguracionMockOpenFinance,
    ErrorManifest,
    ErrorPersistenciaManifest,
    ErrorSerializacionManifest,
    FuenteConfiguracionAdaptador,
    ManifestCorrida,
    ManifestExistenteError,
    construir_manifest,
    guardar_manifest,
    serializar_manifest,
)

__all__ = [
    "ConfiguracionCache",
    "ConfiguracionCarga",
    "ConfiguracionCircuitBreaker",
    "ConfiguracionMockOpenFinance",
    "ErrorManifest",
    "ErrorPersistenciaManifest",
    "ErrorSerializacionManifest",
    "FuenteConfiguracionAdaptador",
    "ManifestCorrida",
    "ManifestExistenteError",
    "construir_manifest",
    "guardar_manifest",
    "serializar_manifest",
]
