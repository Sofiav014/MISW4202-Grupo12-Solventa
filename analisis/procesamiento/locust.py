"""Complementos derivados de los CSV de Locust.

Los CSV de Locust son agregados por endpoint y NO sirven para las metricas del
ASR: no distinguen un exito con datos frescos de una respuesta degradada desde
cache (ambas son HTTP 200), y no contienen estado del circuito, hit/miss ni
tiempo de conmutacion. Esas metricas salen del registro por peticion.

Aqui se usan solo para las dos cosas que el JSONL no da directamente:

1. Throughput (RPS) sostenido en el tiempo, sobre todo en el escenario G.
2. Contraste entre la latencia externa (vista por el cliente, incluye red,
   serializacion y cola del servidor) y la interna (medida dentro del
   Adaptador). La diferencia es el overhead que la instrumentacion interna no
   captura; es un chequeo de consistencia metodologica, no el dato principal.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from analisis.procesamiento.carga import DIRECTORIO_RESULTADOS


FILA_AGREGADA = "Aggregated"


def _escenario_desde_ruta(ruta: Path) -> str:
    return ruta.parent.parent.name.replace("escenario_", "")


def cargar_historico(directorio: Path | None = None) -> pd.DataFrame:
    """Serie temporal de throughput por corrida (results_stats_history.csv)."""
    directorio = directorio or DIRECTORIO_RESULTADOS
    marcos = []
    for ruta in sorted(directorio.glob("escenario_*/*/results_stats_history.csv")):
        datos = pd.read_csv(ruta)
        datos = datos[datos["Name"] == FILA_AGREGADA].copy()
        if datos.empty:
            continue
        datos["escenario"] = _escenario_desde_ruta(ruta)
        datos["ejecucion_id"] = ruta.parent.name
        datos["t_rel_s"] = datos["Timestamp"] - datos["Timestamp"].min()
        marcos.append(datos)

    if not marcos:
        return pd.DataFrame()

    df = pd.concat(marcos, ignore_index=True)
    return df.rename(
        columns={
            "User Count": "usuarios",
            "Requests/s": "rps",
            "Failures/s": "fallos_por_s",
            "Total Request Count": "peticiones_acumuladas",
            "50%": "latencia_externa_p50_ms",
            "95%": "latencia_externa_p95_ms",
        }
    )


def cargar_agregados(directorio: Path | None = None) -> pd.DataFrame:
    """Latencia externa final por corrida (results_stats.csv)."""
    directorio = directorio or DIRECTORIO_RESULTADOS
    registros = []
    for ruta in sorted(directorio.glob("escenario_*/*/results_stats.csv")):
        datos = pd.read_csv(ruta)
        fila = datos[datos["Name"] == FILA_AGREGADA]
        if fila.empty:
            continue
        fila = fila.iloc[0]
        registros.append(
            {
                "escenario": _escenario_desde_ruta(ruta),
                "ejecucion_id": ruta.parent.name,
                "peticiones_locust": int(fila["Request Count"]),
                "fallos_locust": int(fila["Failure Count"]),
                "rps_promedio": float(fila["Requests/s"]),
                "latencia_externa_p50_ms": float(fila["50%"]),
                "latencia_externa_p95_ms": float(fila["95%"]),
                "latencia_externa_max_ms": float(fila["Max Response Time"]),
            }
        )
    return pd.DataFrame(registros)


def throughput_por_escenario(directorio: Path | None = None) -> pd.DataFrame:
    """Throughput sostenido por escenario, promediando sus corridas."""
    agregados = cargar_agregados(directorio)
    if agregados.empty:
        return agregados
    resumen = (
        agregados.groupby("escenario")
        .agg(
            corridas=("ejecucion_id", "size"),
            peticiones_locust=("peticiones_locust", "sum"),
            fallos_locust=("fallos_locust", "sum"),
            rps_medio=("rps_promedio", "mean"),
            rps_std=("rps_promedio", "std"),
        )
        .reset_index()
    )
    return resumen


def contraste_latencia(
    peticiones: pd.DataFrame, directorio: Path | None = None
) -> pd.DataFrame:
    """Compara la latencia externa (Locust) con la interna (Adaptador).

    En la mediana, la latencia externa es mayor que la interna: el cliente ve
    ademas red, serializacion y cola del servidor. Ese overhead (~6-20 ms) es
    el chequeo de consistencia esperado.

    En los percentiles altos la comparacion NO es directa, porque cada fuente
    mide una poblacion distinta de peticiones: el conteo de Locust y el del
    registro interno diveren en ~5 % (ver `diferencia_conteo`), asi que el
    percentil 95 de cada uno no cae sobre la misma peticion. En escenarios
    donde la distribucion es bimodal --como B, con ~94 % de respuestas de
    cache a ~1 ms y ~5 % que pagan el timeout de 700 ms-- un pequeno
    desplazamiento del corte salta de una moda a la otra y produce diferencias
    grandes de signo arbitrario. Por eso `overhead_p95_ms` puede salir
    negativo sin que haya inconsistencia: comparense las medianas, y para la
    cola usese el desglose por poblacion del modulo de metricas.
    """
    externa = cargar_agregados(directorio)
    if externa.empty:
        return externa

    interna = (
        peticiones.groupby(["escenario", "ejecucion_id"])["latencia_total_ms"]
        .agg(
            latencia_interna_p50_ms=lambda s: s.quantile(0.50),
            latencia_interna_p95_ms=lambda s: s.quantile(0.95),
            peticiones_jsonl="size",
        )
        .reset_index()
    )

    tabla = externa.merge(interna, on=["escenario", "ejecucion_id"], how="inner")
    tabla["overhead_p50_ms"] = (
        tabla["latencia_externa_p50_ms"] - tabla["latencia_interna_p50_ms"]
    )
    tabla["overhead_p95_ms"] = (
        tabla["latencia_externa_p95_ms"] - tabla["latencia_interna_p95_ms"]
    )
    tabla["diferencia_conteo"] = tabla["peticiones_locust"] - tabla["peticiones_jsonl"]
    return tabla
