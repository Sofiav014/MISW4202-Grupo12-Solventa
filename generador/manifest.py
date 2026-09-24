"""
Manifest de una corrida: las condiciones efectivas con que se midió.

Una tasa de detección o un percentil de latencia no son interpretables sin
saber contra qué umbral se midieron. El manifest se escribe *antes* de medir y
no se sobrescribe nunca: es evidencia de la corrida, no un archivo de estado.

Registra además si el Detector era el real o un doble, porque una corrida
contra el doble produce cifras tautológicas que nadie debe confundir con
evidencia del experimento.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from parametros import (
    RADIO_UBICACION_KM,
    TASA_TRAFICO_SUPLANTADO,
    UMBRAL_LATENCIA_DETECCION_MS,
    UMBRAL_LATENCIA_MS,
    VENTANA_ACTIVIDAD_RECIENTE_MIN,
)


DETECTOR_REAL = "real"
DETECTOR_DOBLE = "doble"

NOMBRE_ARCHIVO = "manifest.json"


class ErrorManifest(RuntimeError):
    """No se pudo construir o persistir el manifest."""


class ManifestExistenteError(ErrorManifest):
    """Ya hay un manifest para esa corrida; no se sobrescribe."""


def _entero_positivo(nombre: str, valor) -> int:
    """Valida un entero estrictamente positivo, rechazando booleanos."""
    # En Python `isinstance(True, int)` es verdadero, así que un booleano
    # pasaría inadvertido como número de usuarios o de segundos.
    if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
        raise ErrorManifest(f"{nombre} debe ser un entero positivo, se recibió {valor!r}")

    return valor


@dataclass(frozen=True)
class ConfiguracionCarga:
    """Parámetros de carga con que Locust ejecutó la corrida."""

    usuarios: int
    duracion_segundos: int
    spawn_rate: float

    def __post_init__(self):
        _entero_positivo("usuarios", self.usuarios)
        _entero_positivo("duracion_segundos", self.duracion_segundos)

        if not isinstance(self.spawn_rate, (int, float)) or self.spawn_rate <= 0:
            raise ErrorManifest(f"spawn_rate debe ser positivo, se recibió {self.spawn_rate!r}")


@dataclass(frozen=True)
class ConfiguracionDeteccion:
    """Umbrales de decisión vigentes en el Detector durante la corrida."""

    radio_ubicacion_km: float = RADIO_UBICACION_KM
    ventana_actividad_min: int = VENTANA_ACTIVIDAD_RECIENTE_MIN
    presupuesto_deteccion_ms: int = UMBRAL_LATENCIA_DETECCION_MS
    presupuesto_total_ms: int = UMBRAL_LATENCIA_MS


@dataclass(frozen=True)
class ConfiguracionPoblacion:
    """
    Composición del tráfico y de la población sembrada.

    La semilla es lo que hace reproducible una matriz de confusión: sin ella,
    dos corridas del mismo escenario no comparan las mismas sesiones y las
    diferencias entre sus tasas no se pueden atribuir al sistema.
    """

    tamano_dataset: int
    semilla_aleatoria: int
    tasa_trafico_suplantado: float = TASA_TRAFICO_SUPLANTADO
    ttl_sesion_segundos: int = 0
    distancias_frontera: tuple[float, ...] = ()

    def __post_init__(self):
        _entero_positivo("tamano_dataset", self.tamano_dataset)


@dataclass(frozen=True)
class ManifestCorrida:
    """Condiciones completas de una corrida medida."""

    corrida_id: str
    escenario: str
    timestamp: datetime
    detector: str
    carga: ConfiguracionCarga
    deteccion: ConfiguracionDeteccion
    poblacion: ConfiguracionPoblacion

    def __post_init__(self):
        if not self.corrida_id or "/" in self.corrida_id or "\\" in self.corrida_id:
            raise ErrorManifest(f"corrida_id inválido: {self.corrida_id!r}")

        if self.detector not in (DETECTOR_REAL, DETECTOR_DOBLE):
            raise ErrorManifest(
                f"detector debe ser {DETECTOR_REAL!r} o {DETECTOR_DOBLE!r}, "
                f"se recibió {self.detector!r}"
            )

        # Un instante sin zona horaria no fija cuándo ocurrió la corrida.
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ErrorManifest("timestamp debe incluir zona horaria")

    def a_dict(self) -> dict:
        """Proyecta el manifest al objeto JSON que se persiste."""
        return {
            "version": 1,
            "corrida_id": self.corrida_id,
            "escenario": self.escenario,
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat(),
            # Marca deliberada: distingue una corrida con evidencia real de una
            # contra el doble, cuyas cifras son tautológicas.
            "detector": self.detector,
            "carga": {
                "usuarios": self.carga.usuarios,
                "duracion_segundos": self.carga.duracion_segundos,
                "spawn_rate": self.carga.spawn_rate,
            },
            "deteccion": {
                "radio_ubicacion_km": self.deteccion.radio_ubicacion_km,
                "ventana_actividad_min": self.deteccion.ventana_actividad_min,
                "presupuesto_deteccion_ms": self.deteccion.presupuesto_deteccion_ms,
                "presupuesto_total_ms": self.deteccion.presupuesto_total_ms,
            },
            "poblacion": {
                "tamano_dataset": self.poblacion.tamano_dataset,
                "semilla_aleatoria": self.poblacion.semilla_aleatoria,
                "tasa_trafico_suplantado": self.poblacion.tasa_trafico_suplantado,
                "ttl_sesion_segundos": self.poblacion.ttl_sesion_segundos,
                "distancias_frontera": list(self.poblacion.distancias_frontera),
            },
        }


def construir(
    corrida_id: str,
    escenario: str,
    *,
    usuarios: int,
    duracion_segundos: int,
    spawn_rate: float,
    tamano_dataset: int,
    semilla_aleatoria: int,
    ttl_sesion_segundos: int,
    distancias_frontera: tuple[float, ...] = (),
    detector: str = DETECTOR_REAL,
    timestamp: datetime | None = None,
) -> ManifestCorrida:
    """
    Arma el manifest de una corrida con los parámetros efectivos.

    Los umbrales de detección se toman de `parametros`, que ya resuelve las
    variables de entorno, de modo que el manifest refleje lo que el Detector
    está aplicando y no lo que el código trae por defecto.
    """
    return ManifestCorrida(
        corrida_id=corrida_id,
        escenario=escenario,
        timestamp=timestamp or datetime.now(timezone.utc),
        detector=detector,
        carga=ConfiguracionCarga(usuarios, duracion_segundos, spawn_rate),
        deteccion=ConfiguracionDeteccion(),
        poblacion=ConfiguracionPoblacion(
            tamano_dataset=tamano_dataset,
            semilla_aleatoria=semilla_aleatoria,
            ttl_sesion_segundos=ttl_sesion_segundos,
            distancias_frontera=tuple(distancias_frontera),
        ),
    )


def guardar(manifest: ManifestCorrida, directorio: Path) -> Path:
    """
    Persiste el manifest sin sobrescribir uno existente.

    Args:
        manifest: Manifest a escribir.
        directorio: Carpeta de la corrida; se crea si falta.

    Returns:
        La ruta escrita.

    Raises:
        ManifestExistenteError: Si ya existe un manifest para esa corrida. Es
            evidencia ya registrada, y reemplazarla en silencio dejaría métricas
            atribuidas a condiciones que no fueron las suyas.
    """
    directorio = Path(directorio)
    directorio.mkdir(parents=True, exist_ok=True)
    destino = directorio / NOMBRE_ARCHIVO

    contenido = json.dumps(manifest.a_dict(), ensure_ascii=False, indent=2)

    try:
        # Apertura exclusiva: falla si el archivo ya existe.
        with destino.open("x", encoding="utf-8") as archivo:
            archivo.write(contenido + "\n")
    except FileExistsError as error:
        raise ManifestExistenteError(
            f"Ya existe {destino}. Usa otro --ejecucion-id para no confundir "
            f"la evidencia de dos corridas distintas."
        ) from error

    return destino
