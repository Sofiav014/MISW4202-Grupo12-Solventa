"""Orquestacion del guardado independiente del origen y del almacenamiento."""

from __future__ import annotations

import logging
from pathlib import Path

from .contratos import AlmacenResultados, EntradaManifest, ErrorResultados, EvidenciaCorrida
from .validacion import validar_evidencia

logger = logging.getLogger("experimentos.resultados")


def guardar_resultados(evidencia: EvidenciaCorrida, almacen: AlmacenResultados) -> Path:
    """Valida entradas de una corrida terminada y delega exclusivamente su guardado."""
    contexto: dict[str, object] = {"event_type": "results_save_error"}
    if isinstance(evidencia, EvidenciaCorrida) and isinstance(evidencia.manifest, EntradaManifest):
        contexto.update({
            "corrida_id": getattr(evidencia.manifest.modelo, "corrida_id", None),
            "escenario": getattr(evidencia.manifest.modelo, "escenario", None),
        })
    try:
        validar_evidencia(evidencia)
        destino = almacen.guardar(evidencia)
    except ErrorResultados:
        logger.exception("results_save_error", extra=contexto)
        raise
    logger.info("results_saved", extra={
        "event_type": "results_saved", "corrida_id": evidencia.manifest.modelo.corrida_id,
        "escenario": evidencia.manifest.modelo.escenario, "ruta": str(destino),
    })
    return destino
