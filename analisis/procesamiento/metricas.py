"""
Métricas del experimento de seguridad, con las fórmulas del diseño.

Todas las funciones son puras: reciben el DataFrame de peticiones y devuelven
otro con los resultados.

Una métrica cuyo denominador es cero devuelve NaN, nunca cero ni cien, porque
"indefinida" y "cero" son afirmaciones distintas. En el escenario legítimo puro
no hay suplantaciones: la tasa de detección no es del 0 %, simplemente no está
definida, y reportarla como cero afirmaría que el sistema no detectó nada.
"""

import numpy as np
import pandas as pd

from generador.poblacion import ETIQUETA_LEGITIMA, ETIQUETA_SUPLANTADA
from generador.registro import (
    OBSERVADO_ENRUTADA,
    OBSERVADO_ERROR_INFRA,
    OBSERVADO_RECHAZADA,
    OBSERVADO_SUSPENDIDA,
)

from .carga import LLAVES_AGRUPACION


# Desenlaces que constituyen una decisión de clasificación. Un 400 o un 5xx no
# son juicios del Detector sobre la sesión, así que quedan fuera del
# denominador de la matriz y se informan por separado.
OBSERVADOS_DECISION = (OBSERVADO_SUSPENDIDA, OBSERVADO_ENRUTADA)


def _porcentaje(numerador: float, denominador: float) -> float:
    """Porcentaje seguro: denominador cero devuelve NaN, no cero."""
    if denominador == 0:
        return float("nan")

    return numerador / denominador * 100.0


def solo_decisiones(df: pd.DataFrame) -> pd.DataFrame:
    """Descarta las peticiones que no produjeron una decisión de clasificación."""
    return df[df["veredicto_observado"].isin(OBSERVADOS_DECISION)]


def matriz_confusion(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """
    Cruza la verdad-terreno con lo que el sistema decidió.

    Cada tasa usa su propio denominador, que es el punto donde es más fácil
    equivocarse: la tasa de detección se calcula solo sobre las suplantaciones,
    la de falsos positivos solo sobre las legítimas, y la precisión solo sobre
    las que el sistema suspendió. Dividir las cuatro celdas por el total daría
    números que parecen razonables y no responden ninguna pregunta.

    Args:
        df: Peticiones cargadas.
        por: Claves de agrupación; por defecto el escenario.

    Returns:
        Una fila por grupo con las cuatro celdas y las tasas derivadas.
    """
    por = por or ["escenario"]
    decisiones = solo_decisiones(df)

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        suplantadas = grupo["etiqueta_verdad"] == ETIQUETA_SUPLANTADA
        legitimas = grupo["etiqueta_verdad"] == ETIQUETA_LEGITIMA
        suspendidas = grupo["veredicto_observado"] == OBSERVADO_SUSPENDIDA

        # Verdadero positivo: era suplantación y se suspendió.
        vp = int((suplantadas & suspendidas).sum())
        # Falso negativo: era suplantación y pasó al journey.
        fn = int((suplantadas & ~suspendidas).sum())
        # Falso positivo: era legítima y se suspendió.
        fp = int((legitimas & suspendidas).sum())
        # Verdadero negativo: era legítima y pasó.
        vn = int((legitimas & ~suspendidas).sum())

        return pd.Series({
            "decisiones": len(grupo),
            "vp": vp, "fn": fn, "fp": fp, "vn": vn,
            "tasa_deteccion_pct": _porcentaje(vp, vp + fn),
            "tasa_falsos_positivos_pct": _porcentaje(fp, fp + vn),
            "tasa_falsos_negativos_pct": _porcentaje(fn, vp + fn),
            "precision_pct": _porcentaje(vp, vp + fp),
            "exactitud_pct": _porcentaje(vp + vn, len(grupo)),
        })

    if decisiones.empty:
        return pd.DataFrame()

    return decisiones.groupby(por).apply(_calcular, include_groups=False).reset_index()


def latencia(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """
    Resume la latencia observada extremo a extremo.

    Conviene invocarla segmentando por veredicto: una petición suspendida se
    resuelve sin llegar al journey y otra enrutada lo atraviesa, de modo que son
    dos distribuciones distintas. Un percentil que las promedie describe una
    población que no existe.
    """
    por = por or ["escenario"]

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        muestras = grupo["latencia_ms"].dropna()

        if muestras.empty:
            return pd.Series({
                "n": 0, "p50_ms": np.nan, "p95_ms": np.nan,
                "p99_ms": np.nan, "max_ms": np.nan, "media_ms": np.nan,
            })

        return pd.Series({
            "n": len(muestras),
            "p50_ms": muestras.quantile(0.50),
            "p95_ms": muestras.quantile(0.95),
            "p99_ms": muestras.quantile(0.99),
            "max_ms": muestras.max(),
            "media_ms": muestras.mean(),
        })

    if df.empty:
        return pd.DataFrame()

    return df.groupby(por).apply(_calcular, include_groups=False).reset_index()


def suspension_bajo_objetivo(
    df: pd.DataFrame,
    umbral_ms: float,
    por: list[str] | None = None,
) -> pd.DataFrame:
    """
    Mide si las suspensiones ocurrieron dentro del presupuesto de HA16.

    El denominador son únicamente las peticiones efectivamente suspendidas. Es
    la pregunta que la historia plantea —cuánto tarda en suspender— y mezclarla
    con el tráfico que se enrutó respondería otra cosa.

    Args:
        df: Peticiones cargadas.
        umbral_ms: Presupuesto a comparar, en milisegundos.
        por: Claves de agrupación.
    """
    por = por or ["escenario"]

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        suspendidas = grupo[grupo["veredicto_observado"] == OBSERVADO_SUSPENDIDA]
        tiempos = suspendidas["latencia_ms"].dropna()
        n = len(tiempos)
        bajo = int((tiempos < umbral_ms).sum())

        return pd.Series({
            "suspensiones": n,
            "bajo_objetivo": bajo,
            "pct_bajo_objetivo": _porcentaje(bajo, n),
            "suspension_p95_ms": tiempos.quantile(0.95) if n else np.nan,
            "suspension_max_ms": tiempos.max() if n else np.nan,
        })

    if df.empty:
        return pd.DataFrame()

    return df.groupby(por).apply(_calcular, include_groups=False).reset_index()


def deteccion_por_distancia(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tasa de suspensión según el desplazamiento aplicado.

    Es la evidencia central del escenario de frontera: muestra dónde cruza de
    hecho la regla de ubicación y permite contrastarlo con el radio declarado.
    Un cruce lejos del umbral indicaría que el parámetro no describe lo que el
    sistema realmente hace.
    """
    con_distancia = solo_decisiones(df)
    con_distancia = con_distancia[con_distancia["distancia_km"].notna()]

    if con_distancia.empty:
        return pd.DataFrame()

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        suspendidas = int((grupo["veredicto_observado"] == OBSERVADO_SUSPENDIDA).sum())

        return pd.Series({
            "n": len(grupo),
            "suspendidas": suspendidas,
            "pct_suspendidas": _porcentaje(suspendidas, len(grupo)),
            "etiqueta_esperada": grupo["etiqueta_verdad"].iloc[0],
        })

    return (
        con_distancia
        .groupby(["escenario", "distancia_km"])
        .apply(_calcular, include_groups=False)
        .reset_index()
    )


def descartes(df: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """
    Cuenta las peticiones que no produjeron una decisión.

    Se informa aparte porque son el indicador de salud de la corrida: muchos
    rechazos delatan que el generador armó mal las peticiones, y muchos errores
    de infraestructura que el sistema no estaba en condiciones de medirse.
    """
    por = por or ["escenario"]

    def _calcular(grupo: pd.DataFrame) -> pd.Series:
        total = len(grupo)
        rechazadas = int((grupo["veredicto_observado"] == OBSERVADO_RECHAZADA).sum())
        infra = int((grupo["veredicto_observado"] == OBSERVADO_ERROR_INFRA).sum())

        return pd.Series({
            "total": total,
            "rechazadas": rechazadas,
            "errores_infra": infra,
            "pct_sin_decision": _porcentaje(rechazadas + infra, total),
        })

    if df.empty:
        return pd.DataFrame()

    return df.groupby(por).apply(_calcular, include_groups=False).reset_index()


def reproducibilidad(df: pd.DataFrame) -> pd.DataFrame:
    """
    Dispersión de las métricas entre repeticiones del mismo escenario.

    Una tasa de detección alta en una sola corrida no dice si el sistema es
    consistente; la desviación entre repeticiones sí.
    """
    por_corrida = matriz_confusion(df, por=LLAVES_AGRUPACION)

    if por_corrida.empty:
        return pd.DataFrame()

    columnas = [
        "tasa_deteccion_pct",
        "tasa_falsos_positivos_pct",
        "precision_pct",
    ]

    agregado = por_corrida.groupby("escenario")[columnas].agg(["mean", "std", "min", "max"])
    agregado.columns = [f"{columna}_{estadistico}" for columna, estadistico in agregado.columns]
    agregado["corridas"] = por_corrida.groupby("escenario").size()

    return agregado.reset_index()
