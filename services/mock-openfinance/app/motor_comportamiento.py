"""Inyeccion controlada de latencia y fallas para el proveedor simulado."""

import random
import time
from dataclasses import dataclass
from enum import Enum
from threading import Lock


class TipoFallaEnum(str, Enum):
    """Tipos de falla que puede producir el proveedor simulado."""

    HTTP_ERROR = "HTTP_ERROR"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    DROP_CONNECTION = "DROP_CONNECTION"
    NINGUNA = "NINGUNA"


@dataclass(frozen=True)
class ConfiguracionComportamientoDTO:
    """Configuracion inmutable utilizada por el motor de comportamiento."""

    latenciaMinMs: int
    latenciaMaxMs: int
    tasaError: float
    tipoFalla: TipoFallaEnum
    codigoHttpRespuesta: int

    def __post_init__(self) -> None:
        if not self._es_entero(self.latenciaMinMs):
            raise TypeError("latenciaMinMs debe ser un entero")
        if not self._es_entero(self.latenciaMaxMs):
            raise TypeError("latenciaMaxMs debe ser un entero")
        if self.latenciaMinMs < 0 or self.latenciaMaxMs < 0:
            raise ValueError("las latencias no pueden ser negativas")
        if self.latenciaMinMs > self.latenciaMaxMs:
            raise ValueError("latenciaMinMs no puede superar latenciaMaxMs")

        if isinstance(self.tasaError, bool) or not isinstance(
            self.tasaError, (int, float)
        ):
            raise TypeError("tasaError debe ser un numero")
        if not 0.0 <= self.tasaError <= 1.0:
            raise ValueError("tasaError debe estar entre 0.0 y 1.0")
        object.__setattr__(self, "tasaError", float(self.tasaError))

        if not isinstance(self.tipoFalla, TipoFallaEnum):
            raise TypeError("tipoFalla debe ser un TipoFallaEnum valido")
        if not self._es_entero(self.codigoHttpRespuesta):
            raise TypeError("codigoHttpRespuesta debe ser un entero")
        if not 100 <= self.codigoHttpRespuesta <= 599:
            raise ValueError("codigoHttpRespuesta debe estar entre 100 y 599")
        if (
            self.tipoFalla is TipoFallaEnum.HTTP_ERROR
            and not 400 <= self.codigoHttpRespuesta <= 599
        ):
            raise ValueError(
                "codigoHttpRespuesta debe estar entre 400 y 599 para HTTP_ERROR"
            )
        if self.tipoFalla is TipoFallaEnum.NINGUNA and self.tasaError > 0.0:
            raise ValueError("tipoFalla NINGUNA requiere tasaError igual a 0.0")

    @staticmethod
    def _es_entero(valor: object) -> bool:
        return isinstance(valor, int) and not isinstance(valor, bool)


class FallaSimuladaException(RuntimeError):
    """Indica que el motor decidio inyectar una falla controlada."""

    def __init__(
        self,
        tipoFalla: TipoFallaEnum,
        codigoHttpRespuesta: int,
    ) -> None:
        self.tipoFalla = tipoFalla
        self.codigoHttpRespuesta = codigoHttpRespuesta
        super().__init__(f"Falla simulada: {tipoFalla.value}")


class MotorComportamiento:
    """Mantiene y aplica una configuracion de comportamiento thread-safe."""

    def __init__(
        self,
        configuracionInicial: ConfiguracionComportamientoDTO | None = None,
    ) -> None:
        self._lock = Lock()
        self._configuracion_actual = configuracionInicial or self._configuracion_sana()

    @property
    def configuracionActual(self) -> ConfiguracionComportamientoDTO:
        """Devuelve un snapshot inmutable de la configuracion vigente."""
        with self._lock:
            return self._configuracion_actual

    def actualizarConfiguracion(
        self,
        nuevaConfig: ConfiguracionComportamientoDTO,
    ) -> None:
        """Reemplaza atomicamente la configuracion vigente."""
        if not isinstance(nuevaConfig, ConfiguracionComportamientoDTO):
            raise TypeError(
                "nuevaConfig debe ser una ConfiguracionComportamientoDTO"
            )
        with self._lock:
            self._configuracion_actual = nuevaConfig

    def aplicarEfectosDeRed(self) -> None:
        """Pausa el hilo por una latencia aleatoria del rango configurado."""
        configuracion = self.configuracionActual
        latencia_ms = random.randint(
            configuracion.latenciaMinMs,
            configuracion.latenciaMaxMs,
        )
        time.sleep(latencia_ms / 1000)

    def evaluarTasaError(self) -> None:
        """Lanza una falla simulada cuando se cumple la probabilidad configurada."""
        configuracion = self.configuracionActual
        if random.random() < configuracion.tasaError:
            raise FallaSimuladaException(
                configuracion.tipoFalla,
                configuracion.codigoHttpRespuesta,
            )

    @staticmethod
    def _configuracion_sana() -> ConfiguracionComportamientoDTO:
        return ConfiguracionComportamientoDTO(
            latenciaMinMs=50,
            latenciaMaxMs=100,
            tasaError=0.0,
            tipoFalla=TipoFallaEnum.NINGUNA,
            codigoHttpRespuesta=200,
        )
