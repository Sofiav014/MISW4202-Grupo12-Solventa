"""
Figuras del experimento de seguridad.

Se fuerza el backend no interactivo antes de importar pyplot para que las
gráficas se generen igual en una terminal sin entorno de ventanas.
"""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from parametros import RADIO_UBICACION_KM, UMBRAL_LATENCIA_MS  # noqa: E402

from . import metricas  # noqa: E402
from .carga import LLAVES_AGRUPACION  # noqa: E402


DPI = 150


def _guardar(figura, destino, nombre: str):
    """Escribe la figura y devuelve su ruta."""
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / f"{nombre}.png"
    figura.savefig(ruta, dpi=DPI, bbox_inches="tight")
    plt.close(figura)

    return ruta


def matriz_confusion(df: pd.DataFrame, destino):
    """Dibuja las tasas de detección y de falsos positivos por escenario."""
    tabla = metricas.matriz_confusion(df)

    if tabla.empty:
        return None

    figura, eje = plt.subplots(figsize=(8, 4.5))
    posiciones = range(len(tabla))
    ancho = 0.38

    eje.bar([p - ancho / 2 for p in posiciones], tabla["tasa_deteccion_pct"],
            ancho, label="Detección", color="#2a6f97")
    eje.bar([p + ancho / 2 for p in posiciones], tabla["tasa_falsos_positivos_pct"],
            ancho, label="Falsos positivos", color="#c1121f")

    eje.set_xticks(list(posiciones))
    eje.set_xticklabels(tabla["escenario"])
    eje.set_ylabel("Porcentaje")
    eje.set_title("Detección y falsos positivos por escenario")
    eje.set_ylim(0, 105)
    eje.legend()
    eje.grid(axis="y", alpha=0.3)

    # Las barras ausentes corresponden a métricas indefinidas, no a ceros; se
    # rotulan para que el lector no interprete el hueco como un incumplimiento.
    for posicion, (_, fila) in zip(posiciones, tabla.iterrows()):
        if pd.isna(fila["tasa_deteccion_pct"]):
            eje.text(posicion - ancho / 2, 2, "N/A", ha="center", fontsize=8, rotation=90)
        if pd.isna(fila["tasa_falsos_positivos_pct"]):
            eje.text(posicion + ancho / 2, 2, "N/A", ha="center", fontsize=8, rotation=90)

    return _guardar(figura, destino, "matriz_confusion")


def deteccion_por_distancia(df: pd.DataFrame, destino):
    """Traza la curva de detección frente al desplazamiento aplicado."""
    tabla = metricas.deteccion_por_distancia(df)

    if tabla.empty:
        return None

    figura, eje = plt.subplots(figsize=(8, 4.5))

    for escenario, grupo in tabla.groupby("escenario"):
        grupo = grupo.sort_values("distancia_km")
        eje.plot(grupo["distancia_km"], grupo["pct_suspendidas"],
                 marker="o", label=f"Escenario {escenario}")

    eje.axvline(RADIO_UBICACION_KM, color="#c1121f", linestyle="--",
                label=f"Radio declarado ({RADIO_UBICACION_KM:g} km)")
    eje.set_xlabel("Distancia desde la ubicación registrada (km)")
    eje.set_ylabel("% de peticiones suspendidas")
    eje.set_title("Detección según distancia: ¿el umbral discrimina donde dice?")
    eje.set_ylim(-5, 105)
    eje.legend()
    eje.grid(alpha=0.3)

    return _guardar(figura, destino, "deteccion_por_distancia")


def latencia_por_veredicto(df: pd.DataFrame, destino):
    """
    Compara las distribuciones de latencia de cada desenlace.

    Se dibujan separadas porque suspender y enrutar recorren caminos distintos:
    una caja única mezclaría dos poblaciones y su mediana no describiría
    ninguna de las dos.
    """
    decisiones = metricas.solo_decisiones(df)

    if decisiones.empty:
        return None

    grupos, etiquetas = [], []
    for veredicto, grupo in decisiones.groupby("veredicto_observado"):
        muestras = grupo["latencia_ms"].dropna()
        if not muestras.empty:
            grupos.append(muestras.values)
            etiquetas.append(f"{veredicto}\n(n={len(muestras)})")

    if not grupos:
        return None

    figura, eje = plt.subplots(figsize=(7, 4.5))
    eje.boxplot(grupos, tick_labels=etiquetas, showfliers=True)
    eje.axhline(UMBRAL_LATENCIA_MS, color="#c1121f", linestyle="--",
                label=f"Presupuesto HA16 ({UMBRAL_LATENCIA_MS} ms)")
    eje.set_ylabel("Latencia extremo a extremo (ms)")
    eje.set_title("Latencia por veredicto observado")
    eje.legend()
    eje.grid(axis="y", alpha=0.3)

    return _guardar(figura, destino, "latencia_por_veredicto")


def reproducibilidad(df: pd.DataFrame, destino):
    """Muestra la dispersión de la tasa de detección entre repeticiones."""
    por_corrida = metricas.matriz_confusion(df, por=LLAVES_AGRUPACION)

    if por_corrida.empty or por_corrida["escenario"].nunique() == 0:
        return None

    con_deteccion = por_corrida[por_corrida["tasa_deteccion_pct"].notna()]

    if con_deteccion.empty:
        return None

    figura, eje = plt.subplots(figsize=(8, 4.5))

    for escenario, grupo in con_deteccion.groupby("escenario"):
        eje.scatter([escenario] * len(grupo), grupo["tasa_deteccion_pct"], alpha=0.75)

    eje.set_ylabel("Tasa de detección (%)")
    eje.set_title("Dispersión de la detección entre repeticiones")
    eje.set_ylim(-5, 105)
    eje.grid(axis="y", alpha=0.3)

    return _guardar(figura, destino, "reproducibilidad")


def resultado_global(df: pd.DataFrame, destino):
    """
    Resume en una sola figura qué hizo el sistema con cada tipo de petición.

    Está pensada para quien no siguió el experimento: dice, en dos barras, si
    los ataques se detuvieron y si los clientes legítimos pudieron operar.
    """
    decisiones = metricas.solo_decisiones(df)

    if decisiones.empty:
        return None

    from generador.poblacion import ETIQUETA_LEGITIMA, ETIQUETA_SUPLANTADA
    from generador.registro import OBSERVADO_SUSPENDIDA

    grupos = []
    for etiqueta, titulo in (
        (ETIQUETA_SUPLANTADA, "Suplantaciones"),
        (ETIQUETA_LEGITIMA, "Clientes legítimos"),
    ):
        subconjunto = decisiones[decisiones["etiqueta_verdad"] == etiqueta]

        if subconjunto.empty:
            continue

        suspendidas = int((subconjunto["veredicto_observado"] == OBSERVADO_SUSPENDIDA).sum())
        grupos.append((titulo, suspendidas, len(subconjunto) - suspendidas, len(subconjunto)))

    if not grupos:
        return None

    figura, eje = plt.subplots(figsize=(8, 4.5))
    posiciones = range(len(grupos))

    eje.bar(posiciones, [g[1] for g in grupos], label="Operación suspendida", color="#c1121f")
    eje.bar(posiciones, [g[2] for g in grupos], bottom=[g[1] for g in grupos],
            label="Operación permitida", color="#2a9d3f")

    eje.set_xticks(list(posiciones))
    eje.set_xticklabels([f"{g[0]}\n(n={g[3]:,})" for g in grupos])
    eje.set_ylabel("Peticiones")
    eje.set_title("Qué hizo el sistema con cada tipo de petición")
    eje.legend()
    eje.grid(axis="y", alpha=0.3)

    # El porcentaje sobre cada barra evita tener que leer la escala para saber
    # si la separación fue total o solo mayoritaria.
    for posicion, (_, suspendidas, _, total) in zip(posiciones, grupos):
        eje.text(posicion, total * 1.02, f"{100 * suspendidas / total:.0f}% suspendidas",
                 ha="center", fontsize=10, fontweight="bold")

    eje.set_ylim(0, max(g[3] for g in grupos) * 1.15)

    return _guardar(figura, destino, "resultado_global")


def latencia_contra_presupuesto(df: pd.DataFrame, destino):
    """
    Sitúa la latencia de las suspensiones frente al presupuesto de HA16.

    La escala es logarítmica porque las cifras difieren en dos órdenes de
    magnitud: en escala lineal las barras medidas serían invisibles junto a la
    línea del presupuesto, que es justamente lo que hay que mostrar.
    """
    suspensiones = metricas.suspension_bajo_objetivo(df, UMBRAL_LATENCIA_MS)

    if suspensiones.empty:
        return None

    suspensiones = suspensiones[suspensiones["suspensiones"] > 0]

    if suspensiones.empty:
        return None

    figura, eje = plt.subplots(figsize=(8, 4.5))

    eje.bar(suspensiones["escenario"], suspensiones["suspension_p95_ms"],
            color="#2a6f97", label="p95 medido")
    eje.axhline(UMBRAL_LATENCIA_MS, color="#c1121f", linestyle="--", linewidth=2,
                label=f"Presupuesto HA16 ({UMBRAL_LATENCIA_MS} ms)")

    eje.set_yscale("log")
    eje.set_ylabel("Milisegundos (escala logarítmica)")
    eje.set_xlabel("Escenario")
    eje.set_title("Tiempo en detectar y suspender frente al presupuesto")
    eje.legend()
    eje.grid(axis="y", alpha=0.3, which="both")

    for posicion, (_, fila) in enumerate(suspensiones.iterrows()):
        eje.text(posicion, fila["suspension_p95_ms"] * 1.15,
                 f"{fila['suspension_p95_ms']:.0f} ms", ha="center", fontsize=9)

    return _guardar(figura, destino, "latencia_contra_presupuesto")


def generar_todas(df: pd.DataFrame, destino) -> list:
    """Produce todas las figuras que los datos permitan."""
    figuras = [
        resultado_global(df, destino),
        latencia_contra_presupuesto(df, destino),
        matriz_confusion(df, destino),
        deteccion_por_distancia(df, destino),
        latencia_por_veredicto(df, destino),
        reproducibilidad(df, destino),
    ]

    return [figura for figura in figuras if figura is not None]
