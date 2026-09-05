"""Reglas puras de integridad tecnica; no calculan metricas experimentales."""

from __future__ import annotations

import json
import re

from experimentos.manifest import ManifestCorrida

from .contratos import (
    AdjuntoCSV, CAMPOS_TRAZABILIDAD, COLUMNAS_ADJUNTOS, ErrorIntegridadResultados,
    EntradaManifest, EvidenciaCorrida, ProcedenciaRegistros, TablaRegistros,
)
from .entradas import interpretar_csv, interpretar_manifest


def validar_registros(manifest: ManifestCorrida, registros: TablaRegistros) -> None:
    """Exige identidad por fila y rechaza mezclas, sin inferir ventanas de medicion."""
    if not registros.filas:
        raise ErrorIntegridadResultados("resultados vacios: se requiere al menos una peticion")
    columnas = registros.columnas
    if any(not isinstance(c, str) or not c.strip() for c in columnas):
        raise ErrorIntegridadResultados("columnas sin nombre valido")
    if len(set(columnas)) != len(columnas):
        raise ErrorIntegridadResultados("columnas duplicadas")
    faltantes = set(CAMPOS_TRAZABILIDAD) - set(columnas)
    if faltantes:
        raise ErrorIntegridadResultados(f"columnas obligatorias ausentes: {', '.join(sorted(faltantes))}")
    for numero, fila in enumerate(registros.filas, 1):
        for campo in CAMPOS_TRAZABILIDAD:
            valor = fila.get(campo)
            if not isinstance(valor, str) or not valor.strip():
                raise ErrorIntegridadResultados(f"registro {numero}: {campo} obligatorio y no vacio")
        if fila["ejecucion_id"] != manifest.corrida_id:
            raise ErrorIntegridadResultados(f"registro {numero}: ejecucion_id no coincide con corrida_id del manifest")
        if fila["escenario"] != manifest.escenario:
            raise ErrorIntegridadResultados(f"registro {numero}: escenario no coincide con el manifest")
        if "event_type" in columnas and fila.get("event_type") != "request":
            raise ErrorIntegridadResultados(f"registro {numero}: event_type debe ser request")
        if fila.get("logger") in ("werkzeug", "adaptador.cache", "adaptador.circuit_breaker"):
            raise ErrorIntegridadResultados(f"registro {numero}: evento ajeno a las peticiones del adaptador")
        if set(fila) - set(columnas):
            raise ErrorIntegridadResultados(f"registro {numero}: contiene campos no declarados en columnas")
        try:
            json.dumps(dict(fila), ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as error:
            raise ErrorIntegridadResultados(f"registro {numero}: valores no serializables como JSON") from error


def validar_adjunto(manifest: ManifestCorrida, adjunto: AdjuntoCSV) -> None:
    """Comprueba el contrato CSV conocido, admitiendo cabeceras sin fallos."""
    if adjunto.nombre not in COLUMNAS_ADJUNTOS:
        raise ErrorIntegridadResultados(f"nombre de adjunto no permitido: {adjunto.nombre}")
    tabla = interpretar_csv(adjunto.contenido, adjunto.nombre)
    faltantes = set(COLUMNAS_ADJUNTOS[adjunto.nombre]) - set(tabla.columnas)
    if faltantes:
        raise ErrorIntegridadResultados(f"{adjunto.nombre}: columnas obligatorias ausentes: {', '.join(sorted(faltantes))}")
    if not tabla.filas and adjunto.nombre in ("results_stats.csv", "results_stats_history.csv"):
        raise ErrorIntegridadResultados(f"{adjunto.nombre}: resultados vacios")
    for numero, fila in enumerate(tabla.filas, 1):
        nombre = fila.get("Name")
        if isinstance(nombre, str) and nombre != "Aggregated":
            etiqueta = re.fullmatch(r"/cotizar\[([A-G])\]", nombre)
            if etiqueta is None or etiqueta.group(1) != manifest.escenario:
                raise ErrorIntegridadResultados(f"{adjunto.nombre}: registro {numero}: Name no identifica el escenario del manifest")
        for campo, esperado in (
            ("escenario", manifest.escenario), ("ejecucion_id", manifest.corrida_id),
            ("corrida_id", manifest.corrida_id),
        ):
            if campo in fila and fila[campo] != esperado:
                raise ErrorIntegridadResultados(f"{adjunto.nombre}: registro {numero}: {campo} inconsistente")


def validar_evidencia(evidencia: EvidenciaCorrida) -> None:
    """Valida el paquete recibido usando el DTO real y reglas comunes a los lectores."""
    if not isinstance(evidencia, EvidenciaCorrida):
        raise ErrorIntegridadResultados("se requiere una EvidenciaCorrida")
    if not isinstance(evidencia.manifest, EntradaManifest) or not isinstance(evidencia.manifest.modelo, ManifestCorrida):
        raise ErrorIntegridadResultados("se requiere una EntradaManifest con el DTO de 4.3")
    if not isinstance(evidencia.registros, TablaRegistros):
        raise ErrorIntegridadResultados("se requiere una TablaRegistros")
    original = interpretar_manifest(evidencia.manifest.contenido)
    if original.modelo != evidencia.manifest.modelo:
        raise ErrorIntegridadResultados("el DTO del manifest no coincide con su JSON original")
    validar_registros(original.modelo, evidencia.registros)
    if evidencia.procedencia is not None:
        validar_procedencia(evidencia.procedencia)
    nombres: set[str] = set()
    for adjunto in evidencia.adjuntos:
        if not isinstance(adjunto, AdjuntoCSV):
            raise ErrorIntegridadResultados("se requiere un AdjuntoCSV por archivo adicional")
        if adjunto.nombre in nombres:
            raise ErrorIntegridadResultados(f"adjunto duplicado: {adjunto.nombre}")
        nombres.add(adjunto.nombre)
        validar_adjunto(original.modelo, adjunto)


def validar_procedencia(procedencia: ProcedenciaRegistros) -> None:
    """Verifica los metadatos de seleccion sin asumir un productor concreto."""
    if not isinstance(procedencia, ProcedenciaRegistros):
        raise ErrorIntegridadResultados("procedencia debe ser una ProcedenciaRegistros")
    for campo in ("archivo_fuente", "criterio_seleccion"):
        valor = getattr(procedencia, campo)
        if not isinstance(valor, str) or not valor.strip():
            raise ErrorIntegridadResultados(f"procedencia: {campo} obligatorio")
    if not isinstance(procedencia.sha256_fuente, str) or not re.fullmatch(r"[0-9a-f]{64}", procedencia.sha256_fuente):
        raise ErrorIntegridadResultados("procedencia: sha256_fuente invalido")
    if not isinstance(procedencia.ventana_medicion_confirmada, bool):
        raise ErrorIntegridadResultados("procedencia: ventana_medicion_confirmada debe ser booleano")
    if not isinstance(procedencia.limitaciones, tuple) or any(
        not isinstance(limite, str) or not limite.strip() for limite in procedencia.limitaciones
    ):
        raise ErrorIntegridadResultados("procedencia: limitaciones debe ser una tupla de textos")
    if not procedencia.ventana_medicion_confirmada and not procedencia.limitaciones:
        raise ErrorIntegridadResultados("procedencia: declarar limitaciones de la ventana no confirmada")
