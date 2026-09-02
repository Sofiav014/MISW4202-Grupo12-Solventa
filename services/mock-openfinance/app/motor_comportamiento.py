"""Inyeccion controlada de latencia y fallas para el proveedor simulado."""

import random
import time
from dataclasses import dataclass
from threading import Lock, Timer
from typing import Optional


@dataclass(frozen=True)
class ConfiguracionComportamientoDTO:
    """Configuracion inmutable utilizada por el motor de comportamiento."""

    latenciaMinMs: int
    latenciaMaxMs: int
    tasaError: float
    autoRepararSegundos: Optional[int] = None

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

        if self.autoRepararSegundos is not None:
            if not self._es_entero(self.autoRepararSegundos):
                raise TypeError("autoRepararSegundos debe ser un entero")
            if self.autoRepararSegundos <= 0:
                raise ValueError("autoRepararSegundos debe ser mayor que cero")

    @staticmethod
    def _es_entero(valor: object) -> bool:
        return isinstance(valor, int) and not isinstance(valor, bool)


class FabricaModosOpenFinance:
    """Construye las configuraciones soportadas por el proveedor simulado."""

    @staticmethod
    def modo_normal() -> ConfiguracionComportamientoDTO:
        return ConfiguracionComportamientoDTO(
            latenciaMinMs=50,
            latenciaMaxMs=100,
            tasaError=0.0,
        )

    @staticmethod
    def modo_lento() -> ConfiguracionComportamientoDTO:
        return ConfiguracionComportamientoDTO(
            latenciaMinMs=900,
            latenciaMaxMs=1200,
            tasaError=0.0,
        )

    @staticmethod
    def modo_caido() -> ConfiguracionComportamientoDTO:
        return ConfiguracionComportamientoDTO(
            latenciaMinMs=10,
            latenciaMaxMs=30,
            tasaError=1.0,
        )

    @staticmethod
    def modo_caida_temporal(segundos: int) -> ConfiguracionComportamientoDTO:
        return ConfiguracionComportamientoDTO(
            latenciaMinMs=10,
            latenciaMaxMs=30,
            tasaError=1.0,
            autoRepararSegundos=segundos,
        )


class MotorComportamiento:
    """Mantiene y aplica una configuracion de comportamiento thread-safe."""

    def __init__(
        self,
        configuracionInicial: ConfiguracionComportamientoDTO | None = None,
    ) -> None:
        if configuracionInicial is not None and not isinstance(
            configuracionInicial, ConfiguracionComportamientoDTO
        ):
            raise TypeError(
                "configuracionInicial debe ser una ConfiguracionComportamientoDTO"
            )
        self._lock = Lock()
        self._configuracion_actual = FabricaModosOpenFinance.modo_normal()
        self._temporizador_autoreparacion: Optional[Timer] = None
        if configuracionInicial is not None:
            self.actualizarConfiguracion(configuracionInicial)

    @property
    def configuracionActual(self) -> ConfiguracionComportamientoDTO:
        """Devuelve un snapshot inmutable de la configuracion vigente."""
        with self._lock:
            return self._configuracion_actual

    def actualizarConfiguracion(
        self,
        nuevaConfig: ConfiguracionComportamientoDTO,
    ) -> bool:
        """Reemplaza la configuracion si no hay una caida temporal activa."""
        if not isinstance(nuevaConfig, ConfiguracionComportamientoDTO):
            raise TypeError(
                "nuevaConfig debe ser una ConfiguracionComportamientoDTO"
            )

        temporizador = None
        with self._lock:
            # Si estoy ejecutando algo ahora, no puedo cambiar la configuracion
            if self._temporizador_autoreparacion is not None:
                return False

            # Configurar
            self._configuracion_actual = nuevaConfig

            # Si la nueva configuracion es una caida temporal, creo un temporizador para repararla
            if nuevaConfig.autoRepararSegundos is not None:
                temporizador = Timer(
                    nuevaConfig.autoRepararSegundos,
                    self._repararAutomaticamente,
                )
                temporizador.daemon = True
                self._temporizador_autoreparacion = temporizador

        if temporizador is not None:
            temporizador.start()
        return True

    def aplicarEfectosDeRed(self) -> None:
        """Pausa el hilo por una latencia aleatoria del rango configurado."""
        configuracion = self.configuracionActual
        latencia_ms = random.randint(
            configuracion.latenciaMinMs,
            configuracion.latenciaMaxMs,
        )
        time.sleep(latencia_ms / 1000)

    def evaluarTasaError(self) -> bool:
        """Indica si la solicitud debe fallar segun la tasa configurada."""
        configuracion = self.configuracionActual
        return random.random() < configuracion.tasaError

    def _repararAutomaticamente(self) -> None:
        with self._lock:
            self._configuracion_actual = FabricaModosOpenFinance.modo_normal()
            self._temporizador_autoreparacion = None
