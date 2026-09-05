"""Lectores de archivos y conversion sintactica, sin consultar productores vivos."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import cast

from experimentos.manifest import (
    ConfiguracionCache, ConfiguracionCarga, ConfiguracionCircuitBreaker,
    ConfiguracionMockOpenFinance, ManifestCorrida,
)

from .contratos import (
    AdjuntoCSV, EntradaManifest, ErrorEntradaResultados, ProcedenciaRegistros,
    TablaRegistros, ValorJSON,
)


def leer_archivo(ruta: str | Path) -> bytes:
    """Lee una instantanea de un archivo regular y contextualiza errores de IO."""
    try:
        if not isinstance(ruta, (str, Path)) or not str(ruta).strip():
            raise ValueError("la ruta debe ser un string o Path no vacio")
        archivo = Path(ruta)
        if not archivo.is_file():
            raise ErrorEntradaResultados(f"no existe un archivo regular: {archivo}")
        return archivo.read_bytes()
    except (OSError, ValueError, RuntimeError) as error:
        raise ErrorEntradaResultados(f"no se pudo leer {ruta}: {error}") from error


def _texto(contenido: bytes, contexto: str) -> str:
    """Decodifica UTF-8, admitiendo BOM de entrada y rechazando bytes NUL."""
    try:
        texto = contenido.decode("utf-8-sig")
    except (AttributeError, UnicodeError) as error:
        raise ErrorEntradaResultados(f"{contexto}: se requiere contenido UTF-8") from error
    if "\x00" in texto:
        raise ErrorEntradaResultados(f"{contexto}: contiene caracteres NUL")
    return texto


def _objeto_sin_duplicados(pares: list[tuple[str, ValorJSON]]) -> dict[str, ValorJSON]:
    """Evita que json.loads oculte una segunda identidad con la misma clave."""
    resultado: dict[str, ValorJSON] = {}
    for clave, valor in pares:
        if clave in resultado:
            raise ValueError(f"clave JSON duplicada: {clave}")
        resultado[clave] = valor
    return resultado


def _constante_invalida(valor: str) -> None:
    """Rechaza las extensiones NaN/Infinity, que no pertenecen a JSON."""
    raise ValueError(f"constante JSON invalida: {valor}")


def interpretar_json(contenido: bytes, contexto: str) -> ValorJSON:
    """Interpreta JSON estricto sin IO y con ubicacion descriptiva del error."""
    try:
        dato = json.loads(
            _texto(contenido, contexto), object_pairs_hook=_objeto_sin_duplicados,
            parse_constant=_constante_invalida,
        )
        # Tambien detecta exponentes finitos en texto que desbordan a infinito.
        json.dumps(dato, allow_nan=False, ensure_ascii=False).encode("utf-8")
        return cast(ValorJSON, dato)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ErrorEntradaResultados(f"{contexto}: JSON invalido: {error}") from error


def interpretar_manifest(contenido: bytes, contexto: str = "manifest") -> EntradaManifest:
    """Reconstituye los DTO de 4.3 sin defaults experimentales ni cambio de schema."""
    datos = interpretar_json(contenido, contexto)
    try:
        if not isinstance(datos, dict):
            raise TypeError("se requiere un objeto JSON")
        # Los constructores originales son la fuente de validacion del contrato.
        cb = cast(dict[str, object], datos["circuit_breaker"])
        cache = cast(dict[str, object], datos["cache"])
        mock = cast(dict[str, object], datos["mock_openfinance"])
        carga = cast(dict[str, object], datos["carga"])
        modelo = ManifestCorrida(
            corrida_id=cast(str, datos["corrida_id"]),
            escenario=cast(str, datos["escenario"]),
            timestamp=datetime.fromisoformat(cast(str, datos["timestamp"]).replace("Z", "+00:00")),
            circuit_breaker=ConfiguracionCircuitBreaker(
                fail_max=cast(int, cb["fail_max"]),
                reset_timeout_seconds=cast(int, cb["reset_timeout_seconds"]),
            ),
            cache=ConfiguracionCache(ttl_seconds=cast(int, cache["ttl_seconds"])),
            mock_openfinance=ConfiguracionMockOpenFinance(
                modo=cast(str, mock["modo"]),
                auto_repair_seconds=cast(int | None, mock.get("auto_repair_seconds")),
            ),
            carga=ConfiguracionCarga(
                usuarios=cast(int, carga["usuarios"]),
                duration_seconds=cast(int, carga["duration_seconds"]),
                spawn_rate=cast(float | None, carga.get("spawn_rate")),
            ),
            provider_timeout_ms=cast(int, datos["provider_timeout_ms"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
        raise ErrorEntradaResultados(f"{contexto}: manifest invalido: {error}") from error
    return EntradaManifest(modelo, contenido)


def leer_manifest(ruta: str | Path) -> EntradaManifest:
    """Recibe un manifest ya producido por la actividad 4.3."""
    return interpretar_manifest(leer_archivo(ruta), str(ruta))


def interpretar_csv(contenido: bytes, contexto: str) -> TablaRegistros:
    """Lee CSV con cabecera unica y filas completas; no interpreta sus medidas."""
    texto = _texto(contenido, contexto)
    lector = csv.reader(io.StringIO(texto, newline=""), strict=True)
    try:
        columnas = tuple(next(lector, ()))
        if not columnas or any(not c.strip() for c in columnas):
            raise ValueError("cabecera vacia o columnas sin nombre")
        if len(set(columnas)) != len(columnas):
            raise ValueError("columnas duplicadas")
        filas: list[dict[str, ValorJSON]] = []
        for valores in lector:
            if len(valores) != len(columnas):
                raise ValueError(f"linea {lector.line_num}: numero de campos distinto de la cabecera")
            filas.append(dict(zip(columnas, valores)))
    except (csv.Error, ValueError) as error:
        raise ErrorEntradaResultados(f"{contexto}: CSV invalido: {error}") from error
    return TablaRegistros(columnas, tuple(filas))


def leer_registros(ruta: str | Path) -> TablaRegistros:
    """Recibe CSV o JSONL de una sola corrida, sin seleccionar ni inventar filas."""
    contenido = leer_archivo(ruta)
    extension = Path(ruta).suffix.lower()
    if extension == ".csv":
        return interpretar_csv(contenido, str(ruta))
    if extension != ".jsonl":
        raise ErrorEntradaResultados(f"{ruta}: registros requiere extension .csv o .jsonl")
    filas: list[dict[str, ValorJSON]] = []
    columnas: set[str] = set()
    for numero, linea in enumerate(_texto(contenido, str(ruta)).splitlines(), 1):
        dato = interpretar_json(linea.encode("utf-8"), f"{ruta}: linea {numero}")
        if not isinstance(dato, dict):
            raise ErrorEntradaResultados(f"{ruta}: linea {numero}: se requiere un objeto por peticion")
        filas.append(dato)
        columnas.update(dato)
    return TablaRegistros(tuple(sorted(columnas)), tuple(filas))


def leer_adjunto(ruta: str | Path) -> AdjuntoCSV:
    """Conserva los bytes de un CSV externo; el validador verificara su contrato."""
    contenido = leer_archivo(ruta)
    return AdjuntoCSV(Path(ruta).name, contenido)


def interpretar_procedencia(contenido: bytes) -> ProcedenciaRegistros:
    """Recupera metadatos de seleccion del paquete sin consultar el log original."""
    datos = interpretar_json(contenido, "procedencia.json")
    campos = {
        "archivo_fuente", "sha256_fuente", "criterio_seleccion",
        "ventana_medicion_confirmada", "limitaciones",
    }
    if not isinstance(datos, dict) or set(datos) != campos or not isinstance(datos["limitaciones"], list):
        raise ErrorEntradaResultados("procedencia.json: contrato invalido")
    return ProcedenciaRegistros(
        archivo_fuente=cast(str, datos["archivo_fuente"]),
        sha256_fuente=cast(str, datos["sha256_fuente"]),
        criterio_seleccion=cast(str, datos["criterio_seleccion"]),
        ventana_medicion_confirmada=cast(bool, datos["ventana_medicion_confirmada"]),
        limitaciones=tuple(cast(list[str], datos["limitaciones"])),
    )
