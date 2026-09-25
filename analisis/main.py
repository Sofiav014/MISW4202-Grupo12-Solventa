"""
Consolida la evidencia del experimento de seguridad y emite el veredicto.

    python -m analisis.main
    python -m analisis.main --salida /tmp/salidas --sin-graficas

Lee los registros por petición de `resultados/` y escribe las tablas de
métricas. Distingue explícitamente una métrica indefinida de una incumplida:
que un escenario no tenga suplantaciones no significa que no detectara nada.
"""

import argparse
from pathlib import Path
import sys

import pandas as pd

from parametros import UMBRAL_LATENCIA_MS

from .procesamiento import metricas
from .procesamiento.carga import (
    DIRECTORIO_RESULTADOS,
    LLAVES_AGRUPACION,
    cargar_manifiestos,
    cargar_peticiones,
)

SALIDA_POR_DEFECTO = Path(__file__).resolve().parent / "procesamiento" / "salidas"

SEPARADOR = "=" * 78


def _formatear(df: pd.DataFrame) -> str:
    """Rinde una tabla legible, marcando como N/A lo que no está definido."""
    if df.empty:
        return "  (sin datos)"

    return df.to_string(index=False, na_rep="N/A", float_format=lambda v: f"{v:,.2f}")


def _veredicto(confusion: pd.DataFrame, suspensiones: pd.DataFrame) -> list[str]:
    """
    Contrasta los resultados con las metas de HA16.

    Cada línea distingue tres desenlaces: cumplido, incumplido y no aplicable.
    Un escenario sin suplantaciones no puede incumplir una meta de detección, y
    presentarlo como fallo sería leer un NaN como un cero.
    """
    lineas = []

    for _, fila in confusion.iterrows():
        escenario = fila["escenario"]
        deteccion = fila["tasa_deteccion_pct"]
        falsos = fila["tasa_falsos_positivos_pct"]

        if pd.isna(deteccion):
            lineas.append(
                f"  {escenario}: detección N/A — el escenario no incluye suplantaciones."
            )
        else:
            lineas.append(f"  {escenario}: detección {deteccion:.2f}%")

        if pd.isna(falsos):
            lineas.append(
                f"  {escenario}: falsos positivos N/A — no hubo tráfico legítimo."
            )
        else:
            lineas.append(f"  {escenario}: falsos positivos {falsos:.2f}%")

    for _, fila in suspensiones.iterrows():
        escenario = fila["escenario"]

        if fila["suspensiones"] == 0:
            lineas.append(
                f"  {escenario}: presupuesto de {UMBRAL_LATENCIA_MS} ms N/A — "
                f"no hubo suspensiones que cronometrar."
            )
            continue

        pct = fila["pct_bajo_objetivo"]
        estado = "cumple" if pct == 100.0 else "INCUMPLE"
        lineas.append(
            f"  {escenario}: {pct:.2f}% de las suspensiones bajo "
            f"{UMBRAL_LATENCIA_MS} ms ({estado}), p95={fila['suspension_p95_ms']:.2f} ms"
        )

    return lineas


def main(argv=None) -> int:
    """Carga la evidencia, calcula las métricas y escribe las tablas."""
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument("--resultados", type=Path, default=DIRECTORIO_RESULTADOS)
    analizador.add_argument("--salida", type=Path, default=SALIDA_POR_DEFECTO)
    analizador.add_argument("--sin-graficas", action="store_true")
    argumentos = analizador.parse_args(argv)

    resultado = cargar_peticiones(argumentos.resultados)

    if resultado.peticiones.empty:
        print(
            f"No hay evidencia en {argumentos.resultados}. "
            f"Corre primero 'python -m generador.run_escenario'.",
            file=sys.stderr,
        )
        return 1

    argumentos.salida.mkdir(parents=True, exist_ok=True)
    peticiones = resultado.peticiones

    print(SEPARADOR)
    print("CALIDAD DE LA EVIDENCIA")
    print(SEPARADOR)
    print(f"  {resultado.resumen_calidad()}")

    confusion = metricas.matriz_confusion(peticiones)
    confusion_por_corrida = metricas.matriz_confusion(peticiones, por=LLAVES_AGRUPACION)
    # La latencia se segmenta por veredicto: suspender y enrutar son caminos de
    # distinta longitud y sus distribuciones no son comparables entre sí.
    latencia_veredicto = metricas.latencia(peticiones, por=["escenario", "veredicto_observado"])
    suspensiones = metricas.suspension_bajo_objetivo(peticiones, UMBRAL_LATENCIA_MS)
    por_distancia = metricas.deteccion_por_distancia(peticiones)
    sin_decision = metricas.descartes(peticiones)
    repro = metricas.reproducibilidad(peticiones)
    condiciones = cargar_manifiestos(argumentos.resultados)

    tablas = {
        "matriz_confusion.csv": confusion,
        "matriz_confusion_por_corrida.csv": confusion_por_corrida,
        "latencia_por_veredicto.csv": latencia_veredicto,
        "suspension_bajo_objetivo.csv": suspensiones,
        "deteccion_por_distancia.csv": por_distancia,
        "descartes.csv": sin_decision,
        "condiciones_corridas.csv": condiciones,
    }

    escritos = []
    for nombre, tabla in tablas.items():
        if not tabla.empty:
            destino = argumentos.salida / nombre
            tabla.to_csv(destino, index=False)
            escritos.append(destino)

    print()
    print(SEPARADOR)
    print("MATRIZ DE CONFUSIÓN POR ESCENARIO")
    print(SEPARADOR)
    print(_formatear(confusion))

    print()
    print(SEPARADOR)
    print("LATENCIA SEGMENTADA POR VEREDICTO")
    print(SEPARADOR)
    print(_formatear(latencia_veredicto))

    if not por_distancia.empty:
        print()
        print(SEPARADOR)
        print("DETECCIÓN SEGÚN DISTANCIA (frontera del radio)")
        print(SEPARADOR)
        print(_formatear(por_distancia))

    print()
    print(SEPARADOR)
    print("PETICIONES SIN DECISIÓN")
    print(SEPARADOR)
    print(_formatear(sin_decision))

    if not repro.empty:
        print()
        print(SEPARADOR)
        print("REPRODUCIBILIDAD ENTRE REPETICIONES")
        print(SEPARADOR)
        print(_formatear(repro))

    print()
    print(SEPARADOR)
    print("VEREDICTO CONTRA LAS METAS DE HA16")
    print(SEPARADOR)
    for linea in _veredicto(confusion, suspensiones):
        print(linea)

    if not argumentos.sin_graficas:
        try:
            from .procesamiento import graficas

            figuras = graficas.generar_todas(peticiones, argumentos.salida)
            escritos.extend(figuras)
        except ImportError:
            print("\n  (matplotlib no está instalado; se omiten las gráficas)")

    print()
    print(f"Archivos escritos en {argumentos.salida}:")
    for ruta in escritos:
        print(f"  {ruta.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
