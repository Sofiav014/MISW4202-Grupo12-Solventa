"""Operacion guardar-resultados del CLI existente; solo requiere la stdlib."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from .contratos import ErrorResultados, EvidenciaCorrida
from .entradas import leer_adjunto, leer_manifest, leer_registros
from .log_compartido import leer_log_compartido, preparar_corridas_desde_log
from .persistencia import AlmacenResultadosLocal
from .servicio import guardar_resultados


class _FormatoEventos(logging.Formatter):
    """Salida JSON acotada a metadatos de 5.4, sin registros de peticiones."""

    def format(self, record: logging.LogRecord) -> str:
        """Serializa el mensaje descriptivo y los atributos de contexto disponibles."""
        evento = {"level": record.levelname, "message": record.getMessage()}
        for campo in ("event_type", "corrida_id", "escenario", "ruta", "archivo"):
            if hasattr(record, campo):
                evento[campo] = getattr(record, campo)
        return json.dumps(evento, ensure_ascii=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Procesa archivos; devuelve 0 en exito, 1 ante evidencia/IO invalida y 2 por uso."""
    parser = argparse.ArgumentParser(prog="run_escenario.py guardar-resultados", description=__doc__)
    manifests = parser.add_mutually_exclusive_group(required=True)
    manifests.add_argument("--manifest", type=Path, help="Manifest de una sola corrida")
    manifests.add_argument("--manifests-dir", type=Path, help="Raiz con escenario_*/<corrida>/manifest.json")
    fuentes = parser.add_mutually_exclusive_group(required=True)
    fuentes.add_argument("--records", type=Path, help="CSV/JSONL de una sola corrida")
    fuentes.add_argument("--log-compartido", type=Path, help="JSONL estructurado; 5.4 separa las peticiones por identidad")
    parser.add_argument("--adjunto", action="append", type=Path, default=[], help="CSV agregado opcional; puede repetirse")
    parser.add_argument("--resultados-dir", type=Path, default=Path(__file__).resolve().parents[2] / "resultados")
    args = parser.parse_args(argv)
    if args.manifests_dir and (not args.log_compartido or args.adjunto):
        parser.error("--manifests-dir requiere --log-compartido y toma adjuntos de cada carpeta, sin --adjunto")
    logger = logging.getLogger("experimentos.resultados")
    handler = logging.StreamHandler()
    handler.setFormatter(_FormatoEventos())
    nivel, propagar = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        if args.manifests_dir:
            evidencias = preparar_corridas_desde_log(args.manifests_dir, args.log_compartido)
        else:
            manifest = leer_manifest(args.manifest)
            if args.log_compartido:
                evidencia = leer_log_compartido(args.log_compartido).evidencia(manifest)
            else:
                evidencia = EvidenciaCorrida(manifest, leer_registros(args.records))
            evidencias = (replace(evidencia, adjuntos=tuple(leer_adjunto(r) for r in args.adjunto)),)
        almacen = AlmacenResultadosLocal(args.resultados_dir)
        for evidencia in evidencias:
            destino = guardar_resultados(evidencia, almacen)
            print(destino)
            if evidencia.procedencia and not evidencia.procedencia.ventana_medicion_confirmada:
                logger.warning("Paquete con integridad tecnica; ventana de medicion no confirmada. Ver procedencia.json.", extra={
                    "event_type": "results_measurement_window_unconfirmed",
                    "corrida_id": evidencia.manifest.modelo.corrida_id,
                    "escenario": evidencia.manifest.modelo.escenario, "ruta": str(destino),
                })
        return 0
    except ErrorResultados as error:
        logger.error(str(error), extra={"event_type": "results_command_error"})
        return 1
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(nivel)
        logger.propagate = propagar
