from dataclasses import dataclass
import os

from parametros import (
    RADIO_UBICACION_KM,
    UMBRAL_LATENCIA_DETECCION_MS,
    VENTANA_ACTIVIDAD_RECIENTE_MIN,
)


@dataclass(frozen=True)
class ConfiguracionDeteccion:
    """
    Parámetros necesarios para construir ms-deteccion-sesiones.

    Los umbrales de negocio toman como valor predeterminado el acordado en
    ``parametros``, de modo que los cuatro componentes midan contra las
    mismas cifras salvo que se sobreescriban deliberadamente.
    """

    url_identidad: str = "http://localhost:8002"
    url_base_datos: str = "sqlite:///deteccion_clientes.db"
    tiempo_espera_identidad_ms: int = 120
    radio_ubicacion_km: float = RADIO_UBICACION_KM
    ventana_actividad_min: int = VENTANA_ACTIVIDAD_RECIENTE_MIN
    presupuesto_latencia_ms: int = UMBRAL_LATENCIA_DETECCION_MS

    @classmethod
    def desde_entorno(cls) -> "ConfiguracionDeteccion":
        """Construye la configuración leyendo las variables de entorno."""
        return cls(
            url_identidad=os.environ.get("URL_IDENTIDAD", "http://localhost:8002"),
            url_base_datos=os.environ.get("URL_BASE_DATOS_DETECCION", "sqlite:///deteccion_clientes.db"),
            tiempo_espera_identidad_ms=int(os.environ.get("TIEMPO_ESPERA_IDENTIDAD_DETECCION_MS", "120")),
            radio_ubicacion_km=float(os.environ.get("RADIO_UBICACION_KM", RADIO_UBICACION_KM)),
            ventana_actividad_min=int(os.environ.get("VENTANA_ACTIVIDAD_RECIENTE_MIN", VENTANA_ACTIVIDAD_RECIENTE_MIN)),
            presupuesto_latencia_ms=int(os.environ.get("UMBRAL_LATENCIA_DETECCION_MS", UMBRAL_LATENCIA_DETECCION_MS)),
        )
