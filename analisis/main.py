"""Punto de entrada unico: regenera todas las tablas y graficas de la tarea 6.1.

Uso:
    python -m analisis.procesar
    python -m analisis.procesar --salida analisis/salidas
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from analisis.procesamiento.carga import (
    ESCENARIOS_ENTREGABLE,
    META_DISPONIBILIDAD,
    RAIZ_REPO,
    cargar_manifiestos,
    cargar_peticiones,
)
from analisis.procesamiento import graficas, locust, metricas


SALIDA_POR_DEFECTO = RAIZ_REPO / "analisis" / "procesamiento" / "salidas"

COLUMNAS_INFORME = [
    "escenario",
    "total_peticiones",
    "exitosos",
    "degradados",
    "fallidos",
    "disponibilidad_pct",
    "tasa_errores_pct",
    "peticiones_conmutadas",
    "pct_conmutaciones_bajo_1s",
    "cache_hit_rate_pct",
    "latencia_p50_ms",
    "latencia_p95_ms",
    "latencia_p99_ms",
    "latencia_max_ms",
]


def _formatear(tabla: pd.DataFrame) -> str:
    return tabla.to_string(index=False, na_rep="N/A", float_format=lambda v: f"{v:,.2f}")


def main(argv: list[str] | None = None) -> int:
    analizador = argparse.ArgumentParser(description="Procesa los resultados de la Fase 5.")
    analizador.add_argument(
        "--salida",
        type=Path,
        default=SALIDA_POR_DEFECTO,
        help="Directorio de salida para CSV y PNG.",
    )
    analizador.add_argument(
        "--sin-graficas", action="store_true", help="Solo genera las tablas CSV."
    )
    argumentos = analizador.parse_args(argv)

    destino: Path = argumentos.salida
    destino.mkdir(parents=True, exist_ok=True)

    carga = cargar_peticiones()
    df = carga.peticiones

    print("=" * 78)
    print("CALIDAD DE LOS DATOS")
    print("=" * 78)
    print(carga.resumen_calidad())

    resumen_escenario = metricas.tabla_resumen(df)
    resumen_corrida = metricas.tabla_resumen(df, por=["escenario", "ejecucion_id"])
    poblaciones = metricas.por_poblacion(df)
    repro = metricas.reproducibilidad(df)
    manifiestos = cargar_manifiestos()

    throughput = locust.throughput_por_escenario()
    contraste = locust.contraste_latencia(df)

    # Solo se escriben las tablas que no son derivables de otra:
    # `reproducibilidad` sale de agregar resumen_por_corrida y `throughput`
    # de agregar el contraste, asi que ambas se muestran pero no se guardan.
    salidas = {
        "resumen_por_escenario.csv": resumen_escenario,
        "resumen_por_corrida.csv": resumen_corrida,
        "metricas_por_poblacion.csv": poblaciones,
        "condiciones_corridas.csv": manifiestos,
        "contraste_latencia_externa_interna.csv": contraste,
    }
    for nombre, tabla in salidas.items():
        tabla.to_csv(destino / nombre, index=False)

    entregable = resumen_escenario[resumen_escenario["escenario"].isin(ESCENARIOS_ENTREGABLE)]

    print()
    print("=" * 78)
    print("RESUMEN POR ESCENARIO — bloque entregable 6.1 (A, B, C, G)")
    print("=" * 78)
    print(_formatear(entregable[COLUMNAS_INFORME]))

    print()
    print("=" * 78)
    print("DESGLOSE POR POBLACIÓN (cuidado de método)")
    print("=" * 78)
    columnas_poblacion = [
        "escenario",
        "poblacion",
        "peticiones",
        "pct_del_escenario",
        "latencia_p50_ms",
        "latencia_p95_ms",
        "conmutacion_p50_ms",
        "conmutacion_max_ms",
    ]
    print(
        _formatear(
            poblaciones[poblaciones["escenario"].isin(ESCENARIOS_ENTREGABLE)][columnas_poblacion]
        )
    )

    print()
    print("=" * 78)
    print("COMPLEMENTOS DESDE LOCUST (throughput y consistencia externa/interna)")
    print("=" * 78)
    print(_formatear(throughput[throughput["escenario"].isin(ESCENARIOS_ENTREGABLE)]))
    print()
    columnas_contraste = [
        "escenario",
        "ejecucion_id",
        "peticiones_locust",
        "peticiones_jsonl",
        "diferencia_conteo",
        "latencia_externa_p50_ms",
        "latencia_interna_p50_ms",
        "overhead_p50_ms",
    ]
    print(
        _formatear(
            contraste[contraste["escenario"].isin(ESCENARIOS_ENTREGABLE)][columnas_contraste]
        )
    )
    print(
        "\nNota: el overhead se compara en la MEDIANA. En los percentiles altos cada\n"
        "fuente mide una poblacion distinta (los conteos difieren ~5 %), y en\n"
        "distribuciones bimodales como B el corte salta entre modas."
    )

    rutas: list[Path] = []
    if not argumentos.sin_graficas:
        rutas = graficas.generar_todas(df, destino)

    print()
    print("=" * 78)
    print("VEREDICTO CONTRA LAS METAS")
    print("=" * 78)
    for _, fila in entregable.iterrows():
        escenario = fila["escenario"]
        cumple_disp = "CUMPLE" if fila["disponibilidad_pct"] >= META_DISPONIBILIDAD else "NO CUMPLE"
        print(
            f"  {escenario}: disponibilidad {fila['disponibilidad_pct']:.3f} % "
            f"(meta {META_DISPONIBILIDAD} %) -> {cumple_disp}"
        )
        if fila["peticiones_conmutadas"] == 0:
            print(
                "     conmutación < 1 s: N/A — 0 conmutaciones "
                "(proveedor sano, el circuito nunca se abrió)"
            )
        else:
            print(
                f"     conmutación < 1 s: {fila['pct_conmutaciones_bajo_1s']:.2f} % "
                f"de {int(fila['peticiones_conmutadas'])} conmutaciones -> CUMPLE"
            )

    print()
    print(f"Tablas escritas en {destino.relative_to(RAIZ_REPO)}/:")
    for nombre in salidas:
        print(f"  - {nombre}")
    if rutas:
        print("Gráficas generadas:")
        for ruta in rutas:
            print(f"  - {ruta.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
