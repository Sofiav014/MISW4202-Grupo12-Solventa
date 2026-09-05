"""Carga y normalizacion de los resultados experimentales de la Fase 5.

La fuente de verdad para las metricas por peticion es el registro estructurado
del adaptador (``resultados/adaptador.jsonl``), que contiene una fila por
peticion con el esquema congelado en la Fase 0.4. Los CSV de Locust son
agregados por endpoint (conteos y percentiles) y no incluyen estado del
circuito, hit/miss ni tiempo de conmutacion, por lo que aqui solo se usan como
fuente secundaria de throughput.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


logger = logging.getLogger("analisis.procesamiento.carga")

# El modulo vive en analisis/nucleo/, tres niveles bajo la raiz del repositorio.
RAIZ_REPO = Path(__file__).resolve().parent.parent.parent
RUTA_REGISTRO = RAIZ_REPO / "resultados" / "adaptador.jsonl"
DIRECTORIO_RESULTADOS = RAIZ_REPO / "resultados"

LOGGER_PETICIONES = "adaptador.request"

ESCENARIOS = ("A", "B", "C", "D", "E", "F", "G")
ESCENARIOS_ENTREGABLE = ("A", "B", "C", "G")

LLAVES_AGRUPACION = ["escenario", "ejecucion_id"]

UMBRAL_CONMUTACION_MS = 1000.0
META_DISPONIBILIDAD = 99.9
TIMEOUT_PROVEEDOR_MS = 700.0

COLUMNAS_ESQUEMA = (
    "request_id",
    "ejecucion_id",
    "escenario",
    "timestamp_inicio",
    "timestamp_fin",
    "estado_circuito_inicio",
    "estado_circuito_fin",
    "timestamp_deteccion",
    "timestamp_respuesta_cache",
    "proveedor_invocado",
    "fuente_respuesta",
    "hit_miss",
    "latencia_proveedor_ms",
    "tiempo_conmutacion_ms",
    "latencia_total_ms",
    "resultado",
    "tipo_error",
)

COLUMNAS_NUMERICAS = (
    "timestamp_inicio",
    "timestamp_fin",
    "timestamp_deteccion",
    "timestamp_respuesta_cache",
    "latencia_proveedor_ms",
    "tiempo_conmutacion_ms",
    "latencia_total_ms",
)

# Mapeo unico entre los valores emitidos por la instrumentacion y el
# vocabulario congelado en la Fase 0.2. Cualquier divergencia de nombres se
# resuelve aqui y en ningun otro lugar del modulo.
MAPEO_FUENTE_RESPUESTA = {
    "PROVIDER": "proveedor",
    "CACHE": "cache",
    "NONE": "ninguno",
}

# La instrumentacion emite "N/A" en hit_miss para las peticiones servidas por
# el proveedor, que nunca consultan la cache. Se normaliza a nulo para que
# queden fuera del denominador del cache hit rate.
VALOR_HIT_MISS_NO_APLICA = "N/A"

RESULTADOS_VALIDOS = frozenset({"exitoso", "degradado", "fallido"})

POBLACION_TRIGGER = "TRIGGER"
POBLACION_CIRCUITO_ABIERTO = "CIRCUITO_ABIERTO"
POBLACION_NORMAL = "NORMAL"


@dataclass
class ResultadoCarga:
    """DataFrame de peticiones junto con la bitacora de calidad de los datos."""

    peticiones: pd.DataFrame
    descartadas: int = 0
    motivos_descarte: dict[str, int] = field(default_factory=dict)
    incidencias: list[str] = field(default_factory=list)

    def resumen_calidad(self) -> str:
        lineas = [
            f"Peticiones cargadas: {len(self.peticiones)}",
            f"Filas descartadas: {self.descartadas}",
        ]
        for motivo, cuenta in sorted(self.motivos_descarte.items()):
            lineas.append(f"  - {motivo}: {cuenta}")
        for incidencia in self.incidencias:
            lineas.append(f"[incidencia] {incidencia}")
        return "\n".join(lineas)


def _leer_registro(ruta: Path) -> tuple[list[dict], dict[str, int]]:
    """Lee el JSONL y devuelve solo los eventos de peticion."""
    crudas: list[dict] = []
    motivos: dict[str, int] = {}

    with ruta.open(encoding="utf-8") as manejador:
        for linea in manejador:
            linea = linea.strip()
            if not linea:
                continue
            try:
                evento = json.loads(linea)
            except json.JSONDecodeError:
                motivos["json_invalido"] = motivos.get("json_invalido", 0) + 1
                continue
            if evento.get("logger") != LOGGER_PETICIONES:
                # Ruido esperado: werkzeug, cache y circuit_breaker.
                continue
            crudas.append(evento)

    return crudas, motivos


def clasificar_poblacion(df: pd.DataFrame) -> pd.Series:
    """Clasifica cada peticion en TRIGGER, CIRCUITO_ABIERTO o NORMAL.

    Es el "cuidado de metodo" de la Fase 6: la peticion que dispara el corte
    paga el timeout completo del proveedor, mientras que las que encuentran el
    circuito ya abierto van directo a cache. Promediarlas juntas oculta el
    costo real del corte.
    """
    dispara_corte = df["estado_circuito_inicio"].isin(["CLOSED", "HALF_OPEN"]) & (
        df["estado_circuito_fin"] == "OPEN"
    )
    ya_abierto = ~df["proveedor_invocado"].astype(bool)

    poblacion = pd.Series(POBLACION_NORMAL, index=df.index, dtype="object")
    poblacion[ya_abierto] = POBLACION_CIRCUITO_ABIERTO
    poblacion[dispara_corte] = POBLACION_TRIGGER
    return poblacion


def cargar_peticiones(ruta: Path | None = None) -> ResultadoCarga:
    """Carga el registro por peticion, valida el esquema y normaliza tipos."""
    ruta = ruta or RUTA_REGISTRO
    if not ruta.exists():
        raise FileNotFoundError(f"No se encontro el registro de peticiones: {ruta}")

    crudas, motivos = _leer_registro(ruta)
    incidencias: list[str] = []

    if not crudas:
        raise ValueError(f"El registro {ruta} no contiene eventos '{LOGGER_PETICIONES}'")

    df = pd.DataFrame(crudas)
    total_inicial = len(df)

    faltantes = [columna for columna in COLUMNAS_ESQUEMA if columna not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas del esquema congelado: {faltantes}")

    sobrantes = [
        columna
        for columna in df.columns
        if columna not in COLUMNAS_ESQUEMA
        and columna not in {"ts_wall", "level", "logger", "event_type"}
    ]
    if sobrantes:
        incidencias.append(f"Columnas fuera del esquema congelado (ignoradas): {sobrantes}")

    for columna in COLUMNAS_NUMERICAS:
        df[columna] = pd.to_numeric(df[columna], errors="coerce")

    df["proveedor_invocado"] = df["proveedor_invocado"].astype(bool)
    df["ts_wall"] = pd.to_datetime(df["ts_wall"], errors="coerce", utc=True)

    # Vocabulario congelado: la instrumentacion emite mayusculas en ingles.
    df["fuente_respuesta"] = (
        df["fuente_respuesta"].map(MAPEO_FUENTE_RESPUESTA).fillna(df["fuente_respuesta"])
    )
    no_mapeadas = set(df["fuente_respuesta"]) - set(MAPEO_FUENTE_RESPUESTA.values())
    if no_mapeadas:
        incidencias.append(f"Valores de fuente_respuesta sin mapeo: {sorted(no_mapeadas)}")

    # "N/A" no es un miss: son peticiones servidas por el proveedor que nunca
    # consultaron la cache. Deben quedar fuera del denominador del hit rate.
    peticiones_sin_cache = int((df["hit_miss"] == VALOR_HIT_MISS_NO_APLICA).sum())
    df["hit_miss"] = df["hit_miss"].replace(VALOR_HIT_MISS_NO_APLICA, pd.NA)
    if peticiones_sin_cache:
        incidencias.append(
            f"{peticiones_sin_cache} peticiones con hit_miss='N/A' (servidas por el "
            "proveedor): excluidas del denominador del cache hit rate."
        )

    filas_invalidas = ~df["resultado"].isin(RESULTADOS_VALIDOS)
    if filas_invalidas.any():
        motivos["resultado_invalido"] = int(filas_invalidas.sum())
        df = df[~filas_invalidas]

    sin_latencia = df["latencia_total_ms"].isna()
    if sin_latencia.any():
        motivos["latencia_total_nula"] = int(sin_latencia.sum())
        df = df[~sin_latencia]

    escenarios_desconocidos = set(df["escenario"]) - set(ESCENARIOS)
    if escenarios_desconocidos:
        incidencias.append(f"Escenarios fuera de A-G: {sorted(escenarios_desconocidos)}")

    df["poblacion"] = clasificar_poblacion(df)

    # ejecucion_id se repite entre escenarios (run1_rep1..3), por lo que la
    # llave de agrupacion valida es siempre el par (escenario, ejecucion_id).
    if df.groupby("ejecucion_id")["escenario"].nunique().gt(1).any():
        incidencias.append(
            "ejecucion_id se repite entre escenarios: se agrupa por "
            "(escenario, ejecucion_id)."
        )

    df = df.sort_values(["escenario", "ejecucion_id", "timestamp_inicio"]).reset_index(drop=True)

    descartadas = total_inicial - len(df)
    return ResultadoCarga(
        peticiones=df,
        descartadas=descartadas,
        motivos_descarte=motivos,
        incidencias=incidencias,
    )


def cargar_manifiestos(directorio: Path | None = None) -> pd.DataFrame:
    """Carga los manifest.json de cada corrida con las condiciones del experimento."""
    directorio = directorio or DIRECTORIO_RESULTADOS
    registros = []
    for ruta in sorted(directorio.glob("escenario_*/*/manifest.json")):
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        carga = datos.get("carga", {})
        registros.append(
            {
                "escenario": datos.get("escenario"),
                "ejecucion_id": datos.get("corrida_id"),
                "modo_mock": datos.get("mock_openfinance", {}).get("modo"),
                "usuarios": carga.get("usuarios"),
                "duracion_s": carga.get("duration_seconds"),
                "spawn_rate": carga.get("spawn_rate"),
                "timeout_ms": datos.get("provider_timeout_ms"),
                "fail_max": datos.get("circuit_breaker", {}).get("fail_max"),
                "reset_timeout_s": datos.get("circuit_breaker", {}).get("reset_timeout_seconds"),
                "ttl_s": datos.get("cache", {}).get("ttl_seconds"),
            }
        )
    return pd.DataFrame(registros)
