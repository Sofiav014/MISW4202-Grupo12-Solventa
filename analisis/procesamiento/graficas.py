"""Graficas del bloque 6.1: latencia, estado del circuito, conmutacion y disponibilidad."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analisis.procesamiento.carga import (
    ESCENARIOS_ENTREGABLE,
    META_DISPONIBILIDAD,
    POBLACION_CIRCUITO_ABIERTO,
    POBLACION_NORMAL,
    POBLACION_TRIGGER,
    UMBRAL_CONMUTACION_MS,
)
from analisis.procesamiento import locust, metricas


DPI = 150

ETIQUETAS_ESCENARIO = {
    "A": "A · Baseline",
    "B": "B · Lentitud",
    "C": "C · Caída total",
    "D": "D · Circuito abierto",
    "E": "E · Recuperación",
    "F": "F · Cache miss",
    "G": "G · Concurrencia",
}

_ORDEN_POBLACION = (POBLACION_TRIGGER, POBLACION_CIRCUITO_ABIERTO, POBLACION_NORMAL)


def _tiempo_relativo(grupo: pd.DataFrame) -> pd.Series:
    """Segundos transcurridos desde el inicio de la corrida (reloj monotonico)."""
    return grupo["timestamp_inicio"] - grupo["timestamp_inicio"].min()


def _guardar(figura: plt.Figure, destino: Path, nombre: str) -> Path:
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / nombre
    figura.savefig(ruta, dpi=DPI, bbox_inches="tight")
    plt.close(figura)
    return ruta


def latencia_en_el_tiempo(df: pd.DataFrame, destino: Path) -> Path:
    """Latencia total por peticion a lo largo de cada corrida (A, B, C, G)."""
    escenarios = [e for e in ESCENARIOS_ENTREGABLE if e in set(df["escenario"])]
    figura, ejes = plt.subplots(
        len(escenarios), 1, figsize=(11, 2.6 * len(escenarios)), sharex=False
    )
    ejes = np.atleast_1d(ejes)

    for eje, escenario in zip(ejes, escenarios):
        datos = df[df["escenario"] == escenario].copy()
        primera = sorted(datos["ejecucion_id"].unique())[0]
        datos = datos[datos["ejecucion_id"] == primera].copy()
        datos["t_rel"] = _tiempo_relativo(datos)

        for poblacion in _ORDEN_POBLACION:
            sub = datos[datos["poblacion"] == poblacion]
            if sub.empty:
                continue
            eje.scatter(
                sub["t_rel"],
                sub["latencia_total_ms"],
                s=9,
                alpha=0.55,
                label=poblacion,
            )
        # Escala log: en B y C conviven respuestas de ~1 ms (cache, circuito
        # abierto) con timeouts de ~700 ms. En lineal las primeras quedan
        # aplastadas contra el eje y no se distingue 0.5 ms de 30 ms.
        eje.set_yscale("log")
        eje.set_title(f"{ETIQUETAS_ESCENARIO.get(escenario, escenario)} — corrida {primera}")
        eje.set_ylabel("latencia (ms)\nescala log")
        eje.grid(alpha=0.25, which="both")
        eje.legend(loc="upper right", fontsize=8, markerscale=1.6)

    ejes[-1].set_xlabel("tiempo desde el inicio de la corrida (s)")
    figura.suptitle("Latencia total por petición en el tiempo", y=1.002, fontsize=13)
    figura.tight_layout()
    return _guardar(figura, destino, "latencia_en_el_tiempo.png")


def _texto_duracion(milisegundos: float) -> str:
    """Duracion en la unidad que se lee sin conversion mental."""
    if milisegundos >= 1000:
        return f"{milisegundos / 1000:,.1f} segundos"
    if milisegundos >= 10:
        return f"{milisegundos:,.0f} ms"
    return f"{milisegundos:,.1f} ms"


def distribucion_latencia(df: pd.DataFrame, destino: Path) -> Path:
    """Cuanto tarda una peticion en cada escenario, en barras horizontales."""
    escenarios = [e for e in ESCENARIOS_ENTREGABLE if e in set(df["escenario"])]
    series = [df.loc[df["escenario"] == e, "latencia_total_ms"].dropna() for e in escenarios]

    figura, eje = plt.subplots(figsize=(10, 4.6))
    posiciones = np.arange(len(escenarios))[::-1]
    tipicos = [serie.quantile(0.50) for serie in series]

    colores = ["tab:red" if valor >= 1000 else "tab:blue" for valor in tipicos]
    eje.barh(posiciones, tipicos, height=0.55, color=colores)

    for posicion, tipico in zip(posiciones, tipicos):
        eje.annotate(
            _texto_duracion(tipico),
            xy=(tipico, posicion),
            xytext=(8, 0),
            textcoords="offset points",
            va="center",
            fontsize=11,
            fontweight="bold",
        )

    eje.set_yticks(posiciones)
    eje.set_yticklabels([ETIQUETAS_ESCENARIO.get(e, e) for e in escenarios])
    eje.set_xlabel("tiempo que tarda una petición (milisegundos)")
    eje.set_title("¿Cuánto tarda una petición en cada escenario?")
    eje.grid(alpha=0.25, axis="x")
    eje.set_xlim(right=max(tipicos) * 1.25)

    figura.tight_layout()
    return _guardar(figura, destino, "distribucion_latencia.png")


def estado_circuito_y_latencia(df: pd.DataFrame, destino: Path) -> Path:
    """Estado del circuito y latencia para B y C, en bandas separadas.

    El estado va en su propia banda sobre la latencia y no como eje gemelo: una
    linea de estado superpuesta a la nube de puntos se lee como si fuera
    latencia, y hacia eso mismo empujaba el eje log de la izquierda.
    """
    escenarios = [e for e in ("B", "C") if e in set(df["escenario"])]

    # Por escenario: una banda baja para el estado y una alta para la latencia.
    figura, ejes = plt.subplots(
        2 * len(escenarios),
        1,
        figsize=(11, 3.9 * len(escenarios)),
        sharex=False,
        gridspec_kw={"height_ratios": [1, 3.4] * len(escenarios), "hspace": 0.55},
    )
    ejes = np.atleast_1d(ejes)

    codigo_estado = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}

    for indice, escenario in enumerate(escenarios):
        eje_estado = ejes[2 * indice]
        eje = ejes[2 * indice + 1]

        datos = df[df["escenario"] == escenario].copy()
        primera = sorted(datos["ejecucion_id"].unique())[0]
        datos = datos[datos["ejecucion_id"] == primera].copy()
        datos["t_rel"] = _tiempo_relativo(datos)
        disparos = datos[datos["poblacion"] == POBLACION_TRIGGER]

        eje_estado.step(
            datos["t_rel"],
            datos["estado_circuito_fin"].map(codigo_estado),
            where="post",
            color="tab:red",
            linewidth=1.6,
        )
        eje_estado.set_yticks(list(codigo_estado.values()))
        eje_estado.set_yticklabels(list(codigo_estado.keys()), fontsize=8)
        eje_estado.set_ylim(-0.4, 2.4)
        eje_estado.grid(alpha=0.2, axis="y")
        eje_estado.set_title(
            f"{ETIQUETAS_ESCENARIO.get(escenario, escenario)} — corrida {primera} "
            f"({len(disparos)} disparos del corte)",
            fontsize=11,
        )

        eje.scatter(datos["t_rel"], datos["latencia_total_ms"], s=8, alpha=0.45)
        eje.set_ylabel("latencia (ms)\nescala log")
        eje.set_yscale("log")
        eje.grid(alpha=0.25, which="both")

        # Marcas de disparo en el borde inferior: como lineas verticales de
        # altura completa saturaban el panel B (47 disparos).
        if not disparos.empty:
            eje.scatter(
                disparos["t_rel"],
                np.full(len(disparos), eje.get_ylim()[0]),
                marker="^",
                s=28,
                color="tab:orange",
                clip_on=False,
                zorder=5,
                label=f"disparo del corte (n={len(disparos)})",
            )
            eje.legend(loc="upper right", fontsize=8)

        eje_estado.set_xlim(eje.get_xlim())

    # Solo el ultimo panel lleva xlabel: en los intermedios chocaba con el
    # titulo del escenario siguiente.
    ejes[-1].set_xlabel("tiempo desde el inicio de la corrida (s)")

    figura.suptitle("Estado del circuito y latencia en el tiempo", y=0.998, fontsize=13)
    return _guardar(figura, destino, "estado_circuito_y_latencia.png")


def conmutaciones_bajo_objetivo(df: pd.DataFrame, destino: Path) -> Path:
    """Tiempo de conmutacion (p50/p95/max) contra el objetivo de 1 s.

    Se grafica el tiempo real y no el "% bajo 1 s": ese porcentaje da 100 % en
    los dos escenarios que conmutan, y dos barras identicas no dicen nada. El
    margen contra el objetivo — milisegundos frente a los 1000 ms permitidos —
    es lo que hay que ver.
    """
    tabla = metricas.conmutaciones(df)
    tabla = tabla[tabla["escenario"].isin(ESCENARIOS_ENTREGABLE)]
    con_datos = tabla[tabla["peticiones_conmutadas"] > 0]
    sin_datos = tabla[tabla["peticiones_conmutadas"] == 0]

    figura, eje = plt.subplots(figsize=(9.5, 5))
    posiciones = np.arange(len(con_datos))
    ancho = 0.26
    series = (
        ("p50", "conmutacion_p50_ms"),
        ("p95", "conmutacion_p95_ms"),
        ("máximo", "conmutacion_max_ms"),
    )

    for desplazamiento, (nombre, columna) in enumerate(series):
        valores = con_datos[columna].to_numpy(dtype=float)
        barras = eje.bar(posiciones + desplazamiento * ancho, valores, ancho, label=nombre)
        for barra, valor in zip(barras, valores):
            eje.annotate(
                f"{valor:.1f}",
                xy=(barra.get_x() + barra.get_width() / 2, valor),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                fontsize=8,
            )

    eje.axhline(
        UMBRAL_CONMUTACION_MS,
        linestyle="--",
        linewidth=1.4,
        color="tab:red",
        label=f"objetivo: {UMBRAL_CONMUTACION_MS:,.0f} ms",
    )
    eje.set_yscale("log")
    eje.set_ylabel("tiempo de conmutación (ms, escala log)")
    eje.set_xticks(posiciones + ancho)
    eje.set_xticklabels(
        [
            f"{ETIQUETAS_ESCENARIO.get(fila['escenario'], fila['escenario'])}\n"
            f"n={int(fila['peticiones_conmutadas']):,} conmutaciones"
            for _, fila in con_datos.iterrows()
        ]
    )
    eje.set_title("Tiempo de conmutación a cache frente al objetivo de 1 s")
    eje.grid(alpha=0.25, axis="y", which="both")
    eje.set_ylim(top=UMBRAL_CONMUTACION_MS * 8)
    eje.legend(fontsize=9, loc="upper center", ncols=4)

    if not sin_datos.empty:
        nombres = ", ".join(
            ETIQUETAS_ESCENARIO.get(e, e) for e in sin_datos["escenario"]
        )
        eje.annotate(
            f"Sin conmutaciones (el circuito nunca se abre): {nombres}",
            xy=(0.5, -0.19),
            xycoords="axes fraction",
            ha="center",
            fontsize=9,
            style="italic",
        )

    figura.tight_layout()
    return _guardar(figura, destino, "conmutaciones_bajo_1s.png")


def disponibilidad_por_escenario(df: pd.DataFrame, destino: Path) -> Path:
    """Barras de disponibilidad con la linea de meta 99.9 %."""
    tabla = metricas.disponibilidad(df)
    tabla = tabla[tabla["escenario"].isin(ESCENARIOS_ENTREGABLE)]

    figura, eje = plt.subplots(figsize=(9, 5))
    etiquetas = [ETIQUETAS_ESCENARIO.get(e, e) for e in tabla["escenario"]]
    barras = eje.bar(etiquetas, tabla["disponibilidad_pct"])

    eje.axhline(
        META_DISPONIBILIDAD,
        linestyle="--",
        linewidth=1.2,
        color="tab:red",
        label=f"meta: {META_DISPONIBILIDAD} %",
    )
    eje.set_ylabel("disponibilidad experimental (%)")
    eje.set_ylim(99.0, 100.25)
    eje.set_title("Disponibilidad experimental por escenario")
    eje.grid(alpha=0.25, axis="y")
    eje.legend(loc="lower right", fontsize=9)

    for barra, (_, fila) in zip(barras, tabla.iterrows()):
        eje.annotate(
            f"{fila['disponibilidad_pct']:.2f} %\nn={int(fila['total_peticiones'])}",
            xy=(barra.get_x() + barra.get_width() / 2, barra.get_height()),
            xytext=(0, 5),
            textcoords="offset points",
            ha="center",
            fontsize=8,
        )
    figura.tight_layout()
    return _guardar(figura, destino, "disponibilidad_por_escenario.png")


def latencia_por_poblacion(df: pd.DataFrame, destino: Path) -> Path:
    """Costo comparado de TRIGGER vs CIRCUITO_ABIERTO: el cuidado de metodo."""
    tabla = metricas.por_poblacion(df)
    tabla = tabla[tabla["escenario"].isin(("B", "C"))]

    figura, eje = plt.subplots(figsize=(9, 5))
    escenarios = sorted(tabla["escenario"].unique())
    ancho = 0.26
    posiciones = np.arange(len(escenarios))

    for desplazamiento, poblacion in enumerate(_ORDEN_POBLACION):
        alturas = []
        for escenario in escenarios:
            fila = tabla[(tabla["escenario"] == escenario) & (tabla["poblacion"] == poblacion)]
            alturas.append(float(fila["latencia_p50_ms"].iloc[0]) if not fila.empty else np.nan)
        barras = eje.bar(posiciones + desplazamiento * ancho, alturas, ancho, label=poblacion)

        # En escala log la altura no es proporcional al valor: el ojo lee una
        # barra 6x mas alta como si fuera 3x. La cifra sobre cada barra es lo
        # que sostiene la comparacion.
        for barra, altura in zip(barras, alturas):
            if np.isnan(altura):
                continue
            eje.annotate(
                f"{altura:,.1f} ms" if altura >= 10 else f"{altura:,.2f} ms",
                xy=(barra.get_x() + barra.get_width() / 2, altura),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                fontsize=8,
            )

    eje.set_xticks(posiciones + ancho)
    eje.set_xticklabels([ETIQUETAS_ESCENARIO.get(e, e) for e in escenarios])
    eje.set_yscale("log")
    eje.set_ylabel("latencia p50 (ms, escala log)")
    eje.set_title("Costo por población: la petición que corta vs. las que ya encuentran el circuito abierto")
    eje.grid(alpha=0.25, axis="y")
    eje.legend(fontsize=9, loc="upper center", ncols=3)
    # Espacio arriba para que la leyenda no tape las etiquetas de TRIGGER.
    eje.set_ylim(top=eje.get_ylim()[1] * 6)
    figura.tight_layout()
    return _guardar(figura, destino, "latencia_por_poblacion.png")


def throughput_concurrencia(destino: Path) -> Path | None:
    """Throughput (RPS) y usuarios concurrentes en el tiempo, para G.

    Unico dato que aportan los CSV de Locust y que el registro interno no
    calcula directamente: cuantas peticiones por segundo sostuvo el sistema.
    """
    historico = locust.cargar_historico()
    if historico.empty or "G" not in set(historico["escenario"]):
        return None

    datos = historico[historico["escenario"] == "G"]
    figura, eje = plt.subplots(figsize=(11, 5))

    for corrida, grupo in datos.groupby("ejecucion_id"):
        eje.plot(grupo["t_rel_s"], grupo["rps"], linewidth=1.3, label=f"RPS — {corrida}")

    eje.set_xlabel("tiempo desde el inicio de la corrida (s)")
    eje.set_ylabel("peticiones por segundo")
    eje.grid(alpha=0.25)
    eje.set_title("Escenario G · throughput sostenido bajo carga concurrente (fuente: Locust)")

    eje_usuarios = eje.twinx()
    primera = sorted(datos["ejecucion_id"].unique())[0]
    grupo = datos[datos["ejecucion_id"] == primera]
    eje_usuarios.plot(
        grupo["t_rel_s"],
        grupo["usuarios"],
        color="tab:gray",
        linestyle="--",
        linewidth=1.2,
        label="usuarios concurrentes",
    )
    eje_usuarios.set_ylabel("usuarios concurrentes")
    eje_usuarios.set_ylim(0, grupo["usuarios"].max() * 1.25)

    lineas, etiquetas = eje.get_legend_handles_labels()
    l2, e2 = eje_usuarios.get_legend_handles_labels()
    eje.legend(lineas + l2, etiquetas + e2, loc="lower right", fontsize=8)

    figura.tight_layout()
    return _guardar(figura, destino, "throughput_escenario_g.png")


def generar_todas(df: pd.DataFrame, destino: Path) -> list[Path]:
    """Genera las graficas del bloque 6.1 y devuelve las rutas escritas."""
    rutas = [
        latencia_en_el_tiempo(df, destino),
        distribucion_latencia(df, destino),
        estado_circuito_y_latencia(df, destino),
        conmutaciones_bajo_objetivo(df, destino),
        disponibilidad_por_escenario(df, destino),
        latencia_por_poblacion(df, destino),
    ]
    throughput = throughput_concurrencia(destino)
    if throughput is not None:
        rutas.append(throughput)
    return rutas
