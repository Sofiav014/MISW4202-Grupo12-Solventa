"""Representacion y persistencia de metadatos para una corrida experimental.

Este modulo registra las condiciones de una corrida. No configura ni ejecuta
Locust, Redis, el Circuit Breaker o el proveedor simulado.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Protocol, cast


logger = logging.getLogger("experimentos.manifest")

ESCENARIOS_PERMITIDOS = frozenset("ABCDEFG")
MODOS_MOCK_PERMITIDOS = frozenset(
    {"normal", "lento", "caido", "caida_temporal"}
)
_PATRON_CORRIDA_ID = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9_-])?$"
)
_NOMBRES_RESERVADOS_WINDOWS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{numero}" for numero in range(1, 10)),
    *(f"LPT{numero}" for numero in range(1, 10)),
}
_ATRIBUTOS_CONFIGURACION = (
    "EJECUCION_ID",
    "ESCENARIO",
    "FAIL_MAX",
    "RESET_TIMEOUT_S",
    "TTL_S",
    "TIMEOUT_MS",
    "LOG_DIR",
)


class FuenteConfiguracionAdaptador(Protocol):
    """Contrato minimo de la configuracion real propiedad del adaptador."""

    EJECUCION_ID: str
    ESCENARIO: str
    FAIL_MAX: int
    RESET_TIMEOUT_S: int
    TTL_S: int
    TIMEOUT_MS: int
    LOG_DIR: str | None


class ErrorManifest(Exception):
    """Error base al serializar o persistir un manifest de corrida."""


class ErrorSerializacionManifest(ErrorManifest):
    """El manifest no pudo convertirse a JSON."""


class ErrorPersistenciaManifest(ErrorManifest):
    """El manifest no pudo persistirse en el filesystem."""


class ManifestExistenteError(ErrorPersistenciaManifest):
    """La corrida ya tiene un manifest y no debe sobrescribirse."""


def _validar_entero_positivo(nombre: str, valor: object) -> None:
    """Exige un entero positivo y rechaza booleanos como enteros implicitos."""

    if isinstance(valor, bool) or not isinstance(valor, int):
        raise TypeError(f"{nombre} debe ser un entero")
    if valor <= 0:
        raise ValueError(f"{nombre} debe ser mayor que cero")


def _validar_corrida_id(corrida_id: object) -> None:
    """Valida un identificador portable que no pueda alterar la ruta destino."""

    if not isinstance(corrida_id, str):
        raise TypeError("corrida_id debe ser un string")
    if not _PATRON_CORRIDA_ID.fullmatch(corrida_id):
        raise ValueError(
            "corrida_id debe tener entre 1 y 128 caracteres ASCII seguros "
            "y no puede contener separadores de ruta"
        )
    nombre_base = corrida_id.split(".", maxsplit=1)[0].upper()
    if nombre_base in _NOMBRES_RESERVADOS_WINDOWS:
        raise ValueError("corrida_id usa un nombre reservado del filesystem")


def _validar_escenario(escenario: object) -> None:
    """Valida las etiquetas de escenario declaradas por el experimento."""

    if not isinstance(escenario, str):
        raise TypeError("escenario debe ser un string")
    if escenario not in ESCENARIOS_PERMITIDOS:
        raise ValueError("escenario debe ser una letra entre A y G")


@dataclass(frozen=True)
class ConfiguracionCircuitBreaker:
    """Snapshot de los parametros efectivos del Circuit Breaker."""

    fail_max: int
    reset_timeout_seconds: int

    def __post_init__(self) -> None:
        _validar_entero_positivo("fail_max", self.fail_max)
        _validar_entero_positivo(
            "reset_timeout_seconds", self.reset_timeout_seconds
        )


@dataclass(frozen=True)
class ConfiguracionCache:
    """Snapshot de la politica de expiracion de la cache."""

    ttl_seconds: int

    def __post_init__(self) -> None:
        _validar_entero_positivo("ttl_seconds", self.ttl_seconds)


@dataclass(frozen=True)
class ConfiguracionMockOpenFinance:
    """Snapshot suministrado por el Mock o por el orquestador de escenarios.

    El modo se recibe de forma explicita porque el Mock permite cambiarlo en
    caliente. ``auto_repair_seconds`` solo aplica al modo ``caida_temporal``.
    """

    modo: str
    auto_repair_seconds: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.modo, str):
            raise TypeError("modo debe ser un string")
        if self.modo not in MODOS_MOCK_PERMITIDOS:
            modos = ", ".join(sorted(MODOS_MOCK_PERMITIDOS))
            raise ValueError(f"modo debe ser uno de: {modos}")

        if self.modo == "caida_temporal":
            if self.auto_repair_seconds is None:
                raise ValueError(
                    "auto_repair_seconds es obligatorio para caida_temporal"
                )
            _validar_entero_positivo(
                "auto_repair_seconds", self.auto_repair_seconds
            )
        elif self.auto_repair_seconds is not None:
            raise ValueError(
                "auto_repair_seconds solo aplica al modo caida_temporal"
            )


@dataclass(frozen=True)
class ConfiguracionCarga:
    """Contrato temporal para los datos que suministrara Locust/I4.

    Usuarios y duracion son obligatorios. ``spawn_rate`` puede ser ``None``
    exclusivamente cuando el proveedor de carga aun no entrega ese dato.
    """

    usuarios: int
    duration_seconds: int
    spawn_rate: float | None = None

    def __post_init__(self) -> None:
        _validar_entero_positivo("usuarios", self.usuarios)
        _validar_entero_positivo("duration_seconds", self.duration_seconds)

        if self.spawn_rate is None:
            return
        if isinstance(self.spawn_rate, bool) or not isinstance(
            self.spawn_rate, (int, float)
        ):
            raise TypeError("spawn_rate debe ser un numero")
        spawn_rate = float(self.spawn_rate)
        if not isfinite(spawn_rate) or spawn_rate <= 0:
            raise ValueError("spawn_rate debe ser mayor que cero")
        object.__setattr__(self, "spawn_rate", spawn_rate)


@dataclass(frozen=True)
class ManifestCorrida:
    """Metadatos inmutables que describen las condiciones de una corrida."""

    corrida_id: str
    escenario: str
    timestamp: datetime
    circuit_breaker: ConfiguracionCircuitBreaker
    cache: ConfiguracionCache
    mock_openfinance: ConfiguracionMockOpenFinance
    carga: ConfiguracionCarga
    provider_timeout_ms: int

    def __post_init__(self) -> None:
        _validar_corrida_id(self.corrida_id)
        _validar_escenario(self.escenario)
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp debe ser un datetime")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp debe incluir zona horaria")
        if not isinstance(self.circuit_breaker, ConfiguracionCircuitBreaker):
            raise TypeError(
                "circuit_breaker debe ser una ConfiguracionCircuitBreaker"
            )
        if not isinstance(self.cache, ConfiguracionCache):
            raise TypeError("cache debe ser una ConfiguracionCache")
        if not isinstance(self.mock_openfinance, ConfiguracionMockOpenFinance):
            raise TypeError(
                "mock_openfinance debe ser una ConfiguracionMockOpenFinance"
            )
        if not isinstance(self.carga, ConfiguracionCarga):
            raise TypeError("carga debe ser una ConfiguracionCarga")
        _validar_entero_positivo("provider_timeout_ms", self.provider_timeout_ms)


def _cargar_configuracion_adaptador() -> FuenteConfiguracionAdaptador:
    """Importa en diferido la fuente de verdad existente del adaptador."""

    from services.adaptador.app import config as configuracion_adaptador

    return cast(FuenteConfiguracionAdaptador, configuracion_adaptador)


def _obtener_fuente_configuracion(
    fuente: FuenteConfiguracionAdaptador | None,
) -> FuenteConfiguracionAdaptador:
    """Obtiene y verifica estructuralmente el proveedor de configuracion."""

    fuente_efectiva = (
        fuente if fuente is not None else _cargar_configuracion_adaptador()
    )
    faltantes = [
        atributo
        for atributo in _ATRIBUTOS_CONFIGURACION
        if not hasattr(fuente_efectiva, atributo)
    ]
    if faltantes:
        raise TypeError(
            "la fuente de configuracion del adaptador no cumple el contrato; "
            f"faltan: {', '.join(faltantes)}"
        )
    return fuente_efectiva


def construir_manifest(
    mock_openfinance: ConfiguracionMockOpenFinance,
    carga: ConfiguracionCarga,
    *,
    fuente_configuracion: FuenteConfiguracionAdaptador | None = None,
    timestamp: datetime | None = None,
) -> ManifestCorrida:
    """Construye un manifest usando configuracion real y snapshots externos.

    ``fuente_configuracion`` usa por defecto ``services.adaptador.app.config``.
    La inyeccion existe para aislar tests y para permitir que un futuro
    orquestador proporcione un objeto con el mismo contrato.
    """

    if not isinstance(mock_openfinance, ConfiguracionMockOpenFinance):
        raise TypeError(
            "mock_openfinance debe ser una ConfiguracionMockOpenFinance"
        )
    if not isinstance(carga, ConfiguracionCarga):
        raise TypeError("carga debe ser una ConfiguracionCarga")

    fuente = _obtener_fuente_configuracion(fuente_configuracion)
    instante = datetime.now(timezone.utc) if timestamp is None else timestamp
    return ManifestCorrida(
        corrida_id=fuente.EJECUCION_ID,
        escenario=fuente.ESCENARIO,
        timestamp=instante,
        circuit_breaker=ConfiguracionCircuitBreaker(
            fail_max=fuente.FAIL_MAX,
            reset_timeout_seconds=fuente.RESET_TIMEOUT_S,
        ),
        cache=ConfiguracionCache(ttl_seconds=fuente.TTL_S),
        mock_openfinance=mock_openfinance,
        carga=carga,
        provider_timeout_ms=fuente.TIMEOUT_MS,
    )


def _manifest_como_diccionario(manifest: ManifestCorrida) -> dict[str, object]:
    """Proyecta el DTO al contrato JSON sin exponer campos arbitrarios."""

    mock_openfinance: dict[str, object] = {"modo": manifest.mock_openfinance.modo}
    if manifest.mock_openfinance.auto_repair_seconds is not None:
        mock_openfinance["auto_repair_seconds"] = (
            manifest.mock_openfinance.auto_repair_seconds
        )

    carga: dict[str, object] = {
        "usuarios": manifest.carga.usuarios,
        "duration_seconds": manifest.carga.duration_seconds,
    }
    if manifest.carga.spawn_rate is not None:
        carga["spawn_rate"] = manifest.carga.spawn_rate

    timestamp_utc = manifest.timestamp.astimezone(timezone.utc)
    return {
        "corrida_id": manifest.corrida_id,
        "escenario": manifest.escenario,
        "timestamp": timestamp_utc.isoformat().replace("+00:00", "Z"),
        "circuit_breaker": {
            "fail_max": manifest.circuit_breaker.fail_max,
            "reset_timeout_seconds": (
                manifest.circuit_breaker.reset_timeout_seconds
            ),
        },
        "cache": {"ttl_seconds": manifest.cache.ttl_seconds},
        "mock_openfinance": mock_openfinance,
        "carga": carga,
        "provider_timeout_ms": manifest.provider_timeout_ms,
    }


def serializar_manifest(manifest: ManifestCorrida) -> str:
    """Serializa el contrato a JSON UTF-8 legible, sin escribir archivos."""

    if not isinstance(manifest, ManifestCorrida):
        raise TypeError("manifest debe ser un ManifestCorrida")
    try:
        return json.dumps(
            _manifest_como_diccionario(manifest),
            ensure_ascii=False,
            indent=2,
        )
    except (TypeError, ValueError) as error:
        logger.exception(
            "manifest_serialization_error corrida_id=%s",
            manifest.corrida_id,
            extra={
                "event_type": "manifest_serialization_error",
                "corrida_id": manifest.corrida_id,
            },
        )
        raise ErrorSerializacionManifest(
            f"no se pudo serializar el manifest de {manifest.corrida_id}"
        ) from error


def _resolver_directorio_resultados(
    directorio_resultados: str | Path | None,
    fuente_configuracion: FuenteConfiguracionAdaptador | None,
) -> Path:
    """Resuelve una base segura explicita o reutiliza LOG_DIR."""

    valor_directorio: str | Path | None = directorio_resultados
    if valor_directorio is None:
        fuente = _obtener_fuente_configuracion(fuente_configuracion)
        valor_directorio = fuente.LOG_DIR
        if valor_directorio is None:
            raise ValueError(
                "directorio_resultados es obligatorio cuando LOG_DIR no esta configurado"
            )

    if not isinstance(valor_directorio, (str, Path)):
        raise TypeError("directorio_resultados debe ser un string o Path")
    if not str(valor_directorio).strip():
        raise ValueError("directorio_resultados no puede estar vacio")

    try:
        directorio = Path(valor_directorio).resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as error:
        raise ErrorPersistenciaManifest(
            "directorio_resultados no es una ruta valida"
        ) from error

    if directorio == Path(directorio.anchor):
        raise ValueError("directorio_resultados no puede ser la raiz del filesystem")
    if directorio.exists() and not directorio.is_dir():
        raise ErrorPersistenciaManifest(
            f"directorio_resultados no es un directorio: {directorio}"
        )
    return directorio


def _validar_subruta(directorio_base: Path, directorio_corrida: Path) -> Path:
    """Impide que enlaces o segmentos de ruta escapen de la base autorizada."""

    try:
        directorio_resuelto = directorio_corrida.resolve(strict=False)
        directorio_resuelto.relative_to(directorio_base)
    except (OSError, RuntimeError, ValueError) as error:
        raise ErrorPersistenciaManifest(
            "la ruta de la corrida escapa del directorio de resultados"
        ) from error
    return directorio_resuelto


def guardar_manifest(
    manifest: ManifestCorrida,
    *,
    directorio_resultados: str | Path | None = None,
    fuente_configuracion: FuenteConfiguracionAdaptador | None = None,
) -> Path:
    """Persiste ``manifest.json`` sin sobrescribir evidencia existente.

    La ruta es ``<base>/escenario_<A-G>/<corrida_id>/manifest.json``. Si no se
    recibe ``directorio_resultados``, se utiliza ``LOG_DIR`` de la fuente de
    configuracion del adaptador.
    """

    if not isinstance(manifest, ManifestCorrida):
        raise TypeError("manifest debe ser un ManifestCorrida")

    contenido = serializar_manifest(manifest)
    try:
        directorio_base = _resolver_directorio_resultados(
            directorio_resultados, fuente_configuracion
        )
        directorio_corrida = _validar_subruta(
            directorio_base,
            directorio_base
            / f"escenario_{manifest.escenario}"
            / manifest.corrida_id,
        )
    except ErrorPersistenciaManifest:
        logger.exception(
            "manifest_path_error corrida_id=%s",
            manifest.corrida_id,
            extra={
                "event_type": "manifest_filesystem_error",
                "corrida_id": manifest.corrida_id,
            },
        )
        raise
    ruta_manifest = directorio_corrida / "manifest.json"

    try:
        directorio_corrida.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        logger.exception(
            "manifest_directory_error corrida_id=%s path=%s",
            manifest.corrida_id,
            directorio_corrida,
            extra={
                "event_type": "manifest_filesystem_error",
                "corrida_id": manifest.corrida_id,
                "ruta_manifest": str(ruta_manifest),
            },
        )
        raise ErrorPersistenciaManifest(
            f"no se pudo crear el directorio de la corrida {manifest.corrida_id}"
        ) from error

    archivo_creado = False
    try:
        with ruta_manifest.open("x", encoding="utf-8", newline="\n") as archivo:
            archivo_creado = True
            archivo.write(contenido)
            archivo.write("\n")
    except FileExistsError as error:
        logger.error(
            "manifest_already_exists corrida_id=%s path=%s",
            manifest.corrida_id,
            ruta_manifest,
            extra={
                "event_type": "manifest_already_exists",
                "corrida_id": manifest.corrida_id,
                "ruta_manifest": str(ruta_manifest),
            },
        )
        raise ManifestExistenteError(
            f"ya existe un manifest para la corrida {manifest.corrida_id}"
        ) from error
    except (OSError, UnicodeError) as error:
        if archivo_creado:
            try:
                ruta_manifest.unlink(missing_ok=True)
            except OSError:
                logger.exception(
                    "manifest_partial_cleanup_error corrida_id=%s path=%s",
                    manifest.corrida_id,
                    ruta_manifest,
                    extra={
                        "event_type": "manifest_partial_cleanup_error",
                        "corrida_id": manifest.corrida_id,
                        "ruta_manifest": str(ruta_manifest),
                    },
                )
        logger.exception(
            "manifest_write_error corrida_id=%s path=%s",
            manifest.corrida_id,
            ruta_manifest,
            extra={
                "event_type": "manifest_filesystem_error",
                "corrida_id": manifest.corrida_id,
                "ruta_manifest": str(ruta_manifest),
            },
        )
        raise ErrorPersistenciaManifest(
            f"no se pudo escribir el manifest de {manifest.corrida_id}"
        ) from error

    logger.info(
        "manifest_created corrida_id=%s path=%s",
        manifest.corrida_id,
        ruta_manifest,
        extra={
            "event_type": "manifest_created",
            "corrida_id": manifest.corrida_id,
            "ruta_manifest": str(ruta_manifest),
        },
    )
    return ruta_manifest
