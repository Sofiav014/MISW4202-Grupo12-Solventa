"""Adaptador 5.4: separa peticiones de un JSONL compartido, sin ejecutar corridas."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .contratos import (
    CAMPOS_TRAZABILIDAD, COLUMNAS_ADJUNTOS, EntradaManifest, ErrorEntradaResultados,
    ErrorIntegridadResultados, EvidenciaCorrida, ProcedenciaRegistros, TablaRegistros,
    ValorJSON,
)
from .entradas import (
    interpretar_json, leer_adjunto, leer_archivo, leer_manifest,
)
from .validacion import validar_evidencia

_LIMITACIONES_VENTANA = (
    "No se dispone de inicio y fin reales de medicion ni de una marca de fase por peticion. "
    "Se conservan todas las peticiones de la identidad, incluido posible warm-up y preparacion.",
    "El manifest se guarda antes de lanzar Locust y puede reutilizarse; su timestamp no "
    "certifica el inicio real. timestamp_inicio/fin del log son monotonicos; los Timestamp "
    "de stats_history son muestras, no limites de ejecucion.",
    "No se infiere el fin como timestamp del manifest mas duracion configurada. "
    "Ejecuciones distintas que reutilicen escenario y ejecucion_id no son distinguibles.",
)


@dataclass(frozen=True)
class LogCompartido:
    """Instantanea leida una sola vez, indexada por escenario y ejecucion_id."""

    grupos: Mapping[tuple[str, str], TablaRegistros]
    procedencia: ProcedenciaRegistros

    def evidencia(
        self, manifest: EntradaManifest, adjuntos_dir: Path | None = None,
    ) -> EvidenciaCorrida:
        """Selecciona una identidad y aplica el validador comun antes de escribir."""
        modelo = manifest.modelo
        identidad = (modelo.escenario, modelo.corrida_id)
        registros = self.grupos.get(identidad)
        if registros is None:
            raise ErrorIntegridadResultados(
                f"sin peticiones para escenario={modelo.escenario} corrida_id={modelo.corrida_id}"
            )
        adjuntos = () if adjuntos_dir is None else tuple(
            leer_adjunto(adjuntos_dir / nombre)
            for nombre in sorted(COLUMNAS_ADJUNTOS)
            if (adjuntos_dir / nombre).exists()
        )
        evidencia = EvidenciaCorrida(manifest, registros, adjuntos, self.procedencia)
        validar_evidencia(evidencia)
        return evidencia


def leer_log_compartido(ruta: str | Path) -> LogCompartido:
    """Lee JSONL estricto y agrupa solo eventos request; nunca fabrica registros.

    Todos los eventos deben ser JSON legible. Los eventos auxiliares se omiten
    explicitamente; una peticion sin identidad o sin clasificacion es un error.
    No aplica filtros temporales: el contrato actual no ofrece una ventana real.
    """
    contenido = leer_archivo(ruta)
    grupos: dict[tuple[str, str], list[dict[str, ValorJSON]]] = {}
    columnas: dict[tuple[str, str], set[str]] = {}
    # Separar por LF, no por separadores Unicode legales dentro de strings JSON.
    lineas = contenido.split(b"\n")
    if lineas[-1] == b"":
        lineas.pop()
    for numero, linea in enumerate(lineas, 1):
        contexto = f"{ruta}: linea {numero}"
        dato = interpretar_json(linea, contexto)
        if not isinstance(dato, dict):
            raise ErrorEntradaResultados(f"{contexto}: se requiere un objeto JSON")
        if dato.get("event_type") != "request":
            if dato.get("logger") == "adaptador.request" or (
                dato.get("event_type") is None and not dato.get("logger")
                and "ejecucion_id" in dato
            ):
                raise ErrorIntegridadResultados(
                    f"{contexto}: peticion sin event_type=request; no puede omitirse"
                )
            continue
        for campo in CAMPOS_TRAZABILIDAD:
            valor = dato.get(campo)
            if not isinstance(valor, str) or not valor.strip():
                raise ErrorIntegridadResultados(f"{contexto}: {campo} obligatorio y no vacio")
        escenario, ejecucion = str(dato["escenario"]), str(dato["ejecucion_id"])
        identidad = (escenario, ejecucion)
        grupos.setdefault(identidad, []).append(dato)
        columnas.setdefault(identidad, set()).update(dato)
    if not grupos:
        raise ErrorIntegridadResultados(f"{ruta}: log sin peticiones")
    return LogCompartido(
        {identidad: TablaRegistros(tuple(sorted(columnas[identidad])), tuple(filas))
         for identidad, filas in grupos.items()},
        ProcedenciaRegistros(
            archivo_fuente=Path(ruta).name,
            sha256_fuente=hashlib.sha256(contenido).hexdigest(),
            criterio_seleccion=(
                "event_type=request; escenario y ejecucion_id coinciden con el manifest; "
                "sin filtro temporal"
            ),
            ventana_medicion_confirmada=False,
            limitaciones=_LIMITACIONES_VENTANA,
        ),
    )


def preparar_corridas_desde_log(
    directorio_manifests: str | Path, ruta_log: str | Path,
) -> tuple[EvidenciaCorrida, ...]:
    """Prevalida todas las entradas de escenario_*/<corrida>/ antes de publicar.

    Exige manifest para cada carpeta de corrida y para cada identidad del log;
    no descarta identidades huerfanas ni carpetas con evidencia incompleta.
    """
    try:
        raiz = Path(directorio_manifests)
        if not raiz.is_dir():
            raise ErrorEntradaResultados(f"directorio de manifests inexistente: {raiz}")
        carpetas = sorted(p for p in raiz.glob("escenario_*/*") if p.is_dir())
        if not carpetas:
            raise ErrorEntradaResultados(f"sin carpetas de corridas en {raiz}")
        manifests: dict[tuple[str, str], tuple[EntradaManifest, Path]] = {}
        for carpeta in carpetas:
            manifest = leer_manifest(carpeta / "manifest.json")
            modelo = manifest.modelo
            if carpeta.name != modelo.corrida_id or carpeta.parent.name != f"escenario_{modelo.escenario}":
                raise ErrorIntegridadResultados(f"carpeta y manifest inconsistentes: {carpeta}")
            identidad = (modelo.escenario, modelo.corrida_id)
            if identidad in manifests:
                raise ErrorIntegridadResultados(f"manifest duplicado para {identidad}")
            manifests[identidad] = (manifest, carpeta)
        log = leer_log_compartido(ruta_log)
        huerfanas = set(log.grupos) - set(manifests)
        if huerfanas:
            raise ErrorIntegridadResultados(f"identidades del log sin manifest: {sorted(huerfanas)}")
        return tuple(log.evidencia(*manifests[identidad]) for identidad in sorted(manifests))
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        raise ErrorEntradaResultados(f"no se pudieron leer las corridas: {error}") from error
