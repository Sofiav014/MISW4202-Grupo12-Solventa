"""Metricas del experimento HA2, con las formulas del esquema de instrumentacion.

Todas las funciones son puras: reciben el DataFrame de peticiones y devuelven
un DataFrame de resultados. Una metrica cuyo denominador es cero devuelve NaN
(nunca 0 ni 100), porque "indefinida" y "cero" son afirmaciones distintas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analisis.procesamiento.carga import (
    LLAVES_AGRUPACION,
    META_DISPONIBILIDAD,
    POBLACION_CIRCUITO_ABIERTO,
    POBLACION_TRIGGER,
    UMBRAL_CONMUTACION_MS,
)


def _porcentaje(numerador: float, denominador: float) -> float:
    """Porcentaje seguro: denominador cero devuelve NaN, no cero."""
    if denominador == 0:
        return float("nan")
    return numerador / denominador * 100.0


def disponibilidad(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """Disponibilidad experimental = (exitosos + degradados) / total x 100.

    Un journey servido desde cache cuenta como degradado exitoso aunque tarde
    mas de 1 s: la disponibilidad y el % de conmutaciones <1 s son metricas
    distintas y no se mezclan.
    """
    por = por or ["escenario"]

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        total = len(grupo)
        exitosos = int((grupo["resultado"] == "exitoso").sum())
        degradados = int((grupo["resultado"] == "degradado").sum())
        fallidos = int((grupo["resultado"] == "fallido").sum())
        return pd.Series(
            {
                "total_peticiones": total,
                "exitosos": exitosos,
                "degradados": degradados,
                "fallidos": fallidos,
                "disponibilidad_pct": _porcentaje(exitosos + degradados, total),
                "tasa_errores_pct": _porcentaje(fallidos, total),
                "cumple_meta_disponibilidad": _porcentaje(exitosos + degradados, total)
                >= META_DISPONIBILIDAD,
            }
        )

    return df.groupby(por).apply(_calcular, include_groups=False).reset_index()


def conmutaciones(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """% de conmutaciones <1 s sobre las peticiones que EFECTIVAMENTE conmutaron.

    El denominador son solo las peticiones con tiempo_conmutacion_ms no nulo,
    nunca el total. Un escenario sin conmutaciones devuelve NaN.
    """
    por = por or ["escenario"]

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        conmutaron = grupo["tiempo_conmutacion_ms"].notna()
        n_conmutaron = int(conmutaron.sum())
        tiempos = grupo.loc[conmutaron, "tiempo_conmutacion_ms"]
        bajo_objetivo = int((tiempos < UMBRAL_CONMUTACION_MS).sum())
        return pd.Series(
            {
                "peticiones_conmutadas": n_conmutaron,
                "conmutaciones_bajo_1s": bajo_objetivo,
                "pct_conmutaciones_bajo_1s": _porcentaje(bajo_objetivo, n_conmutaron),
                "conmutacion_p50_ms": tiempos.quantile(0.50) if n_conmutaron else np.nan,
                "conmutacion_p95_ms": tiempos.quantile(0.95) if n_conmutaron else np.nan,
                "conmutacion_max_ms": tiempos.max() if n_conmutaron else np.nan,
            }
        )

    return df.groupby(por).apply(_calcular, include_groups=False).reset_index()


def cache_hit_rate(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """Cache hit rate = HIT / (HIT + MISS) x 100.

    Las peticiones servidas por el proveedor llegan con hit_miss nulo (la
    carga normaliza el "N/A" de la instrumentacion) y quedan fuera del
    denominador: nunca consultaron la cache.
    """
    por = por or ["escenario"]

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        consultas = grupo["hit_miss"].dropna()
        hits = int((consultas == "HIT").sum())
        misses = int((consultas == "MISS").sum())
        return pd.Series(
            {
                "cache_hits": hits,
                "cache_misses": misses,
                "consultas_cache": hits + misses,
                "cache_hit_rate_pct": _porcentaje(hits, hits + misses),
            }
        )

    return df.groupby(por).apply(_calcular, include_groups=False).reset_index()


def latencia(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """Distribucion de latencia_total_ms: p50, p95, p99 y maximo."""
    por = por or ["escenario"]

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        serie = grupo["latencia_total_ms"].dropna()
        if serie.empty:
            return pd.Series(
                {
                    "latencia_n": 0,
                    "latencia_p50_ms": np.nan,
                    "latencia_p95_ms": np.nan,
                    "latencia_p99_ms": np.nan,
                    "latencia_max_ms": np.nan,
                    "latencia_media_ms": np.nan,
                }
            )
        return pd.Series(
            {
                "latencia_n": len(serie),
                "latencia_p50_ms": serie.quantile(0.50),
                "latencia_p95_ms": serie.quantile(0.95),
                "latencia_p99_ms": serie.quantile(0.99),
                "latencia_max_ms": serie.max(),
                "latencia_media_ms": serie.mean(),
            }
        )

    return df.groupby(por).apply(_calcular, include_groups=False).reset_index()


def por_poblacion(df: pd.DataFrame) -> pd.DataFrame:
    """Desglose por poblacion: TRIGGER, CIRCUITO_ABIERTO y NORMAL.

    La peticion que dispara
    el corte paga el timeout completo del proveedor; las que encuentran el
    circuito ya abierto van directo a cache y cuestan ordenes de magnitud
    menos. El agregado por escenario oculta esa diferencia.
    """
    llaves = ["escenario", "poblacion"]
    tabla = latencia(df, por=llaves)
    tabla = tabla.merge(conmutaciones(df, por=llaves), on=llaves, how="left")
    conteos = df.groupby(llaves).size().rename("peticiones").reset_index()
    tabla = conteos.merge(tabla, on=llaves, how="left")
    total_escenario = df.groupby("escenario").size().rename("total_escenario")
    tabla = tabla.merge(total_escenario, on="escenario", how="left")
    tabla["pct_del_escenario"] = tabla["peticiones"] / tabla["total_escenario"] * 100.0
    return tabla.drop(columns=["total_escenario"])


def desglose_trigger(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """Separa la poblacion TRIGGER en el disparo inicial y los reintentos HALF_OPEN.

    Con fail_max=1 basta una falla para abrir el circuito, pero mientras el
    proveedor sigue degradado el corte no es un evento unico: cada
    reset_timeout, pybreaker deja pasar una peticion de prueba en HALF_OPEN: si
    el proveedor sigue caido, esa prueba vuelve a pagar el costo completo de la
    falla y reabre el circuito. Las dos cosas quedan clasificadas como TRIGGER
    porque las dos tumban el circuito a OPEN, pero conviene distinguirlas: el
    disparo inicial ocurre una vez por corrida; los reintentos se repiten
    mientras dure la degradacion y son la fuga real del "circuito abierto
    evita llamadas al proveedor".
    """
    por = por or ["escenario"]
    trigger = df[df["poblacion"] == POBLACION_TRIGGER]

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        return pd.Series(
            {
                "disparo_inicial": int((grupo["estado_circuito_inicio"] == "CLOSED").sum()),
                "reintentos_half_open": int(
                    (grupo["estado_circuito_inicio"] == "HALF_OPEN").sum()
                ),
            }
        )

    disparos = trigger.groupby(por).apply(_calcular, include_groups=False).reset_index()
    abiertas = (
        df[df["poblacion"] == POBLACION_CIRCUITO_ABIERTO]
        .groupby(por)
        .size()
        .rename("circuito_abierto")
        .reset_index()
    )
    tabla = disparos.merge(abiertas, on=por, how="outer")
    columnas_conteo = ["disparo_inicial", "reintentos_half_open", "circuito_abierto"]
    tabla[columnas_conteo] = tabla[columnas_conteo].fillna(0).astype(int)
    tabla["pct_fuga_reintentos"] = tabla.apply(
        lambda fila: _porcentaje(
            fila["reintentos_half_open"], fila["reintentos_half_open"] + fila["circuito_abierto"]
        ),
        axis=1,
    )
    return tabla


def tabla_resumen(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """Une todas las metricas en una sola tabla por escenario o por corrida."""
    por = por or ["escenario"]
    tabla = disponibilidad(df, por=por)
    for metrica in (conmutaciones, cache_hit_rate, latencia):
        tabla = tabla.merge(metrica(df, por=por), on=por, how="left")

    tabla["cumple_meta_conmutacion"] = np.where(
        tabla["peticiones_conmutadas"] > 0,
        tabla["pct_conmutaciones_bajo_1s"] >= 100.0,
        None,
    )
    return tabla


def reproducibilidad(df: pd.DataFrame) -> pd.DataFrame:
    """Media y dispersion de las metricas entre las corridas de cada escenario."""
    por_corrida = tabla_resumen(df, por=LLAVES_AGRUPACION)
    columnas = [
        "disponibilidad_pct",
        "tasa_errores_pct",
        "pct_conmutaciones_bajo_1s",
        "cache_hit_rate_pct",
        "latencia_p50_ms",
        "latencia_p95_ms",
    ]
    agregado = por_corrida.groupby("escenario")[columnas].agg(["mean", "std", "min", "max"])
    agregado.columns = [f"{columna}_{estadistico}" for columna, estadistico in agregado.columns]
    agregado["corridas"] = por_corrida.groupby("escenario").size()
    return agregado.reset_index()
