"""
Lectura de la evidencia cruda del experimento.

La fuente de verdad son los registros por petición que escribe el generador,
no los CSV de Locust: solo el registro lleva la etiqueta de verdad-terreno, y
sin ella un 403 no se distingue de otro.

La carga valida el esquema antes de consolidar. Un archivo con columnas
distintas o de otra versión se rechaza en vez de producir métricas silenciosas
sobre datos que no son los que se cree.
"""

from dataclasses import dataclass, field
import json
from pathlib import Path

import pandas as pd

from generador.registro import COLUMNAS, ESQUEMA_VERSION, NOMBRE_ARCHIVO
from generador.manifest import NOMBRE_ARCHIVO as NOMBRE_MANIFEST


RAIZ_REPO = Path(__file__).resolve().parent.parent.parent
DIRECTORIO_RESULTADOS = RAIZ_REPO / "resultados"

# Claves por las que se agrupan las métricas. Una repetición es la unidad de
# reproducibilidad: comparar entre ellas es lo que muestra si una tasa es
# estable o producto del azar de una corrida.
LLAVES_AGRUPACION = ["escenario", "ejecucion_id"]

COLUMNAS_NUMERICAS = ("latencia_ms", "ts_envio", "ts_respuesta", "distancia_km", "status")


@dataclass
class ResultadoCarga:
    """
    Lo cargado, junto con lo que se descartó y por qué.

    Se informa el descarte en vez de ocultarlo: una corrida donde se cayó un
    tercio de las peticiones puede dar tasas perfectamente plausibles y aun así
    no ser evidencia de nada.
    """

    peticiones: pd.DataFrame
    descartadas: int = 0
    motivos_descarte: dict[str, int] = field(default_factory=dict)
    incidencias: list[str] = field(default_factory=list)

    def resumen_calidad(self) -> str:
        """Describe en una línea qué tan completo quedó el conjunto."""
        total = len(self.peticiones) + self.descartadas

        if total == 0:
            return "Sin peticiones cargadas."

        partes = [f"{len(self.peticiones)} peticiones cargadas de {total}"]

        if self.descartadas:
            detalle = ", ".join(f"{k}={v}" for k, v in sorted(self.motivos_descarte.items()))
            partes.append(f"{self.descartadas} descartadas ({detalle})")

        for incidencia in self.incidencias:
            partes.append(incidencia)

        return " | ".join(partes)


def cargar_peticiones(directorio: Path | None = None) -> ResultadoCarga:
    """
    Lee todos los registros por petición bajo el directorio de resultados.

    Args:
        directorio: Raíz de la evidencia; por defecto `resultados/` del repo.

    Returns:
        El conjunto consolidado, con el recuento de lo descartado.
    """
    directorio = Path(directorio or DIRECTORIO_RESULTADOS)
    filas = []
    motivos: dict[str, int] = {}
    incidencias: list[str] = []

    archivos = sorted(directorio.glob(f"escenario_*/*/{NOMBRE_ARCHIVO}"))

    if not archivos:
        return ResultadoCarga(pd.DataFrame(columns=list(COLUMNAS)))

    for archivo in archivos:
        for numero, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
            if not linea.strip():
                continue

            try:
                registro = json.loads(linea)
            except json.JSONDecodeError:
                motivos["json_invalido"] = motivos.get("json_invalido", 0) + 1
                continue

            # Una versión distinta significa otras columnas; consolidar sería
            # mezclar dos contratos y atribuir a unas métricas datos de otras.
            if registro.get("esquema_version") != ESQUEMA_VERSION:
                motivos["version_incompatible"] = motivos.get("version_incompatible", 0) + 1
                continue

            faltantes = set(COLUMNAS) - set(registro)
            if faltantes:
                motivos["columnas_faltantes"] = motivos.get("columnas_faltantes", 0) + 1
                continue

            sobrantes = set(registro) - set(COLUMNAS)
            if sobrantes:
                aviso = f"{archivo.parent.name}: columnas no previstas {sorted(sobrantes)}"
                if aviso not in incidencias:
                    incidencias.append(aviso)

            filas.append(registro)

    descartadas = sum(motivos.values())

    if not filas:
        return ResultadoCarga(
            pd.DataFrame(columns=list(COLUMNAS)), descartadas, motivos, incidencias
        )

    df = pd.DataFrame(filas)

    for columna in COLUMNAS_NUMERICAS:
        df[columna] = pd.to_numeric(df[columna], errors="coerce")

    # Una petición sin latencia no aporta a ninguna métrica y desordenaría los
    # percentiles si se contara como cero.
    sin_latencia = df["latencia_ms"].isna().sum()
    if sin_latencia:
        motivos["latencia_ausente"] = int(sin_latencia)
        descartadas += int(sin_latencia)
        df = df[df["latencia_ms"].notna()]

    duplicados = df["request_id"].duplicated().sum()
    if duplicados:
        incidencias.append(f"{duplicados} request_id repetidos (¿se corrió dos veces el mismo id?)")

    df = df.sort_values(["escenario", "ejecucion_id", "ts_envio"]).reset_index(drop=True)

    return ResultadoCarga(df, descartadas, motivos, incidencias)


def cargar_manifiestos(directorio: Path | None = None) -> pd.DataFrame:
    """
    Reúne las condiciones declaradas de cada corrida.

    Sin esta tabla una tasa no es interpretable: no se sabría contra qué radio
    ni con qué semilla se midió, ni si el Detector era el real o un doble.
    """
    directorio = Path(directorio or DIRECTORIO_RESULTADOS)
    filas = []

    for archivo in sorted(directorio.glob(f"escenario_*/*/{NOMBRE_MANIFEST}")):
        contenido = json.loads(archivo.read_text(encoding="utf-8"))

        filas.append({
            "escenario": contenido["escenario"],
            "ejecucion_id": contenido["corrida_id"],
            "timestamp": contenido["timestamp"],
            "detector": contenido["detector"],
            "usuarios": contenido["carga"]["usuarios"],
            "duracion_s": contenido["carga"]["duracion_segundos"],
            "radio_km": contenido["deteccion"]["radio_ubicacion_km"],
            "ventana_min": contenido["deteccion"]["ventana_actividad_min"],
            "presupuesto_total_ms": contenido["deteccion"]["presupuesto_total_ms"],
            "tamano_dataset": contenido["poblacion"]["tamano_dataset"],
            "semilla": contenido["poblacion"]["semilla_aleatoria"],
        })

    return pd.DataFrame(filas)
