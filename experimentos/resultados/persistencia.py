"""Paquetes locales sin sobrescritura y con constancia de integridad final."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import stat
import tempfile
from dataclasses import asdict
from pathlib import Path

from .contratos import (
    AdjuntoCSV, CAMPOS_TRAZABILIDAD, COLUMNAS_ADJUNTOS, ErrorIntegridadResultados,
    ErrorPersistenciaResultados, EvidenciaCorrida, PaqueteExistenteError,
    TablaRegistros, ValorJSON,
)
from .entradas import (
    interpretar_csv, interpretar_json, interpretar_manifest, interpretar_procedencia,
    leer_archivo,
)
from .validacion import validar_adjunto, validar_evidencia, validar_registros

logger = logging.getLogger("experimentos.resultados")
_ARCHIVOS_EVIDENCIA = frozenset({
    "manifest.json", "results.csv", "procedencia.json", *COLUMNAS_ADJUNTOS,
})


def _json_bytes(dato: object) -> bytes:
    """Serializa metadatos deterministicamente, con UTF-8 y salto final."""
    return (json.dumps(dato, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _celda(valor: ValorJSON) -> str:
    """Representa nulos como vacio y valores JSON no textuales como JSON compacto."""
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor
    return json.dumps(valor, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def serializar_registros(registros: TablaRegistros) -> bytes:
    """Escribe todas las columnas, conservando orden de filas y sin calcular medidas."""
    columnas = (*CAMPOS_TRAZABILIDAD, *sorted(set(registros.columnas) - set(CAMPOS_TRAZABILIDAD)))
    salida = io.StringIO(newline="")
    escritor = csv.writer(salida, lineterminator="\n")
    escritor.writerow(columnas)
    for fila in registros.filas:
        escritor.writerow([_celda(fila.get(columna)) for columna in columnas])
    return salida.getvalue().encode("utf-8")


def _es_enlace(ruta: Path) -> bool:
    """Reconoce symlinks y reparse points de Windows, incluidos junctions."""
    datos = ruta.lstat()
    return stat.S_ISLNK(datos.st_mode) or bool(
        getattr(datos, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _ruta_segura(base: Path, ruta: Path) -> Path:
    """Comprueba contencion y rechaza enlaces en todos los segmentos bajo base."""
    try:
        partes = ruta.relative_to(base).parts
        ruta.resolve(strict=False).relative_to(base)
        actual = base
        for parte in partes:
            actual = actual / parte
            if os.path.lexists(actual) and _es_enlace(actual):
                raise ValueError("no se permiten enlaces en el destino de evidencia")
    except (OSError, ValueError, RuntimeError) as error:
        raise ErrorPersistenciaResultados(f"ruta de evidencia insegura: {ruta}: {error}") from error
    return ruta


def _raiz_resultados(valor: str | Path) -> Path:
    """Resuelve una raiz explicita; nunca lee LOG_DIR ni configuracion del adaptador."""
    try:
        if not isinstance(valor, (str, Path)) or not str(valor).strip():
            raise ValueError("directorio_resultados debe ser un string o Path no vacio")
        raiz = Path(valor).resolve(strict=False)
        if raiz == Path(raiz.anchor):
            raise ValueError("no se permite la raiz del filesystem")
        if raiz.exists() and not raiz.is_dir():
            raise ValueError("la raiz de resultados no es un directorio")
        return raiz
    except (OSError, ValueError, RuntimeError) as error:
        raise ErrorPersistenciaResultados(f"directorio_resultados invalido: {error}") from error


def _escribir_temporal(ruta: Path, contenido: bytes) -> None:
    """Completa y sincroniza un archivo temporal antes de hacerlo visible."""
    with ruta.open("xb") as archivo:
        archivo.write(contenido)
        archivo.flush()
        os.fsync(archivo.fileno())


def _publicar_archivo(origen: Path, destino: Path) -> None:
    """Publica bytes completos usando un hard link atomico que falla si ya existe."""
    os.link(origen, destino, follow_symlinks=False)


class AlmacenResultadosLocal:
    """Completa una carpeta por escenario/corrida, conservando evidencia preexistente.

    Requiere un filesystem local con hard links. El productor debe haber terminado
    de escribir; el lock solo coordina a los escritores de evidencia, no al
    proceso que ejecuta el experimento.
    """

    def __init__(self, directorio_resultados: str | Path) -> None:
        """Recibe la raiz de salida sin crear directorios todavia."""
        self.raiz = _raiz_resultados(directorio_resultados)

    def guardar(self, evidencia: EvidenciaCorrida) -> Path:
        """Publica evidencia validada por guardar_resultados; marca integridad al final."""
        # Tambien protege el uso directo del almacen publico.
        validar_evidencia(evidencia)
        modelo = evidencia.manifest.modelo
        destino = _ruta_segura(
            self.raiz, self.raiz / f"escenario_{modelo.escenario}" / modelo.corrida_id,
        )
        contexto = {"corrida_id": modelo.corrida_id, "escenario": modelo.escenario, "ruta": str(destino)}
        bloqueo = destino / ".guardar-resultados.lock"
        bloqueado = False
        temporal: Path | None = None
        creados: list[Path] = []
        completado = False
        try:
            destino.mkdir(parents=True, exist_ok=True)
            _ruta_segura(self.raiz, destino)
            _ruta_segura(self.raiz, bloqueo)
            with bloqueo.open("xb"):
                bloqueado = True
            for nombre in ("results.csv", "integridad.json", "procedencia.json"):
                ruta = _ruta_segura(self.raiz, destino / nombre)
                if os.path.lexists(ruta):
                    raise PaqueteExistenteError(f"no se sobrescribe evidencia existente: {ruta}")

            archivos = {
                "manifest.json": evidencia.manifest.contenido,
                "results.csv": serializar_registros(evidencia.registros),
                **{a.nombre: a.contenido for a in evidencia.adjuntos},
            }
            if evidencia.procedencia is not None:
                archivos["procedencia.json"] = _json_bytes(asdict(evidencia.procedencia))
            validar_registros(modelo, interpretar_csv(archivos["results.csv"], "results.csv"))
            # Incorpora adjuntos ya generados por el experimento sin moverlos ni modificarlos.
            for nombre in COLUMNAS_ADJUNTOS:
                ruta = _ruta_segura(self.raiz, destino / nombre)
                if os.path.lexists(ruta):
                    contenido = leer_archivo(ruta)
                    if nombre in archivos and archivos[nombre] != contenido:
                        raise PaqueteExistenteError(f"adjunto existente distinto del recibido: {ruta}")
                    validar_adjunto(modelo, AdjuntoCSV(nombre, contenido))
                    archivos[nombre] = contenido

            existentes: set[str] = set()
            for nombre, contenido in archivos.items():
                ruta = _ruta_segura(self.raiz, destino / nombre)
                if os.path.lexists(ruta):
                    if leer_archivo(ruta) != contenido:
                        raise PaqueteExistenteError(f"archivo existente distinto del recibido: {ruta}")
                    existentes.add(nombre)

            informe = {
                "version": 1, "estado": "valido", "corrida_id": modelo.corrida_id,
                "escenario": modelo.escenario, "cantidad_registros": len(evidencia.registros.filas),
                "archivos": {nombre: hashlib.sha256(contenido).hexdigest() for nombre, contenido in archivos.items()},
            }
            temporal = Path(tempfile.mkdtemp(prefix=".5_4-", dir=destino))
            _ruta_segura(self.raiz, temporal)
            nuevos = {nombre: contenido for nombre, contenido in archivos.items() if nombre not in existentes}
            nuevos["integridad.json"] = _json_bytes(informe)
            for nombre, contenido in nuevos.items():
                _escribir_temporal(temporal / nombre, contenido)
            for nombre in sorted(set(nuevos) - {"integridad.json"}):
                ruta = _ruta_segura(self.raiz, destino / nombre)
                _publicar_archivo(temporal / nombre, ruta)
                creados.append(ruta)

            # No emitir constancia sobre bytes que otro proceso haya cambiado.
            for nombre, contenido in archivos.items():
                ruta = _ruta_segura(self.raiz, destino / nombre)
                if leer_archivo(ruta) != contenido:
                    raise ErrorPersistenciaResultados(f"archivo modificado durante el guardado: {ruta}")
            observados = {n for n in COLUMNAS_ADJUNTOS if os.path.lexists(destino / n)}
            if observados != set(archivos) & set(COLUMNAS_ADJUNTOS):
                raise ErrorPersistenciaResultados("los adjuntos cambiaron durante el guardado")
            ruta_informe = _ruta_segura(self.raiz, destino / "integridad.json")
            _publicar_archivo(temporal / "integridad.json", ruta_informe)
            creados.append(ruta_informe)
            completado = True
        except FileExistsError as error:
            raise PaqueteExistenteError(f"destino ocupado; no se sobrescribe evidencia: {destino}") from error
        except (OSError, UnicodeError, ValueError) as error:
            raise ErrorPersistenciaResultados(f"no se pudo escribir el paquete {destino}: {error}") from error
        finally:
            if not completado:
                for ruta in reversed(creados):
                    try:
                        _ruta_segura(self.raiz, ruta)
                        if temporal is not None and os.path.samefile(ruta, temporal / ruta.name):
                            ruta.unlink()
                        else:
                            logger.error("results_cleanup_replaced_file", extra={**contexto, "event_type": "results_cleanup_replaced_file", "archivo": str(ruta)})
                    except (OSError, ErrorPersistenciaResultados):
                        logger.exception("results_cleanup_error", extra={**contexto, "event_type": "results_cleanup_error", "archivo": str(ruta)})
            if temporal is not None:
                try:
                    # Verificacion absoluta antes de la unica eliminacion recursiva.
                    _ruta_segura(self.raiz, temporal)
                    shutil.rmtree(temporal)
                except (OSError, ErrorPersistenciaResultados):
                    logger.exception("results_cleanup_error", extra={**contexto, "event_type": "results_cleanup_error", "archivo": str(temporal)})
            if bloqueado:
                try:
                    _ruta_segura(self.raiz, bloqueo).unlink()
                except (OSError, ErrorPersistenciaResultados):
                    logger.exception("results_cleanup_error", extra={**contexto, "event_type": "results_cleanup_error", "archivo": str(bloqueo)})
        return destino


def validar_paquete(ruta: str | Path) -> EvidenciaCorrida:
    """Relee un paquete sin modificarlo y verifica inventario, hashes y contenido."""
    try:
        directorio = Path(ruta).absolute()
        if _es_enlace(directorio) or _es_enlace(directorio.parent):
            raise ErrorIntegridadResultados("la carpeta de evidencia no puede ser un enlace")
        directorio = directorio.resolve(strict=True)
        informe = interpretar_json(leer_archivo(_ruta_segura(directorio, directorio / "integridad.json")), "integridad.json")
        campos = {"version", "estado", "corrida_id", "escenario", "cantidad_registros", "archivos"}
        if not isinstance(informe, dict) or set(informe) != campos:
            raise ErrorIntegridadResultados("integridad.json: contrato de constancia invalido")
        if type(informe["version"]) is not int or informe["version"] != 1 or informe["estado"] != "valido":
            raise ErrorIntegridadResultados("integridad.json: version o estado invalido")
        inventario = informe["archivos"]
        if not isinstance(inventario, dict) or not {"manifest.json", "results.csv"} <= set(inventario) <= _ARCHIVOS_EVIDENCIA:
            raise ErrorIntegridadResultados("integridad.json: inventario incompleto o nombres no permitidos")
        presentes = {n for n in _ARCHIVOS_EVIDENCIA if os.path.lexists(directorio / n)}
        if presentes != set(inventario):
            raise ErrorIntegridadResultados("inventario no coincide con los archivos presentes")
        archivos: dict[str, bytes] = {}
        for nombre, esperado in inventario.items():
            contenido = leer_archivo(_ruta_segura(directorio, directorio / nombre))
            if not isinstance(esperado, str) or not re.fullmatch(r"[0-9a-f]{64}", esperado) or hashlib.sha256(contenido).hexdigest() != esperado:
                raise ErrorIntegridadResultados(f"SHA-256 no coincide: {nombre}")
            archivos[nombre] = contenido
        evidencia = EvidenciaCorrida(
            interpretar_manifest(archivos["manifest.json"]),
            interpretar_csv(archivos["results.csv"], "results.csv"),
            tuple(AdjuntoCSV(n, archivos[n]) for n in sorted(set(archivos) & set(COLUMNAS_ADJUNTOS))),
            interpretar_procedencia(archivos["procedencia.json"]) if "procedencia.json" in archivos else None,
        )
        validar_evidencia(evidencia)
        modelo = evidencia.manifest.modelo
        if informe["corrida_id"] != modelo.corrida_id or informe["escenario"] != modelo.escenario:
            raise ErrorIntegridadResultados("identidad de integridad.json no coincide con el manifest")
        if directorio.name != modelo.corrida_id or directorio.parent.name != f"escenario_{modelo.escenario}":
            raise ErrorIntegridadResultados("la estructura de carpetas no coincide con el manifest")
        if type(informe["cantidad_registros"]) is not int or informe["cantidad_registros"] != len(evidencia.registros.filas):
            raise ErrorIntegridadResultados("cantidad_registros no coincide con results.csv")
        return evidencia
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        raise ErrorIntegridadResultados(f"no se pudo verificar el paquete {ruta}: {error}") from error
