from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ConfiguracionIdentidad:
    """Parámetros necesarios para construir el servicio de Identidad."""

    secreto_jwt: str
    algoritmo_jwt: str = "HS256"
    url_base_datos: str = "sqlite:///identidad_sesiones.db"
    ttl_sesion_segundos: int = 3600

    @classmethod
    def desde_entorno(cls) -> "ConfiguracionIdentidad":
        """Construye la configuración leyendo las variables de entorno del proceso."""
        return cls(
            secreto_jwt=os.environ["SECRETO_JWT"],
            algoritmo_jwt=os.environ.get("ALGORITMO_JWT", "HS256"),
            url_base_datos=os.environ.get("URL_BASE_DATOS_IDENTIDAD", "sqlite:///identidad_sesiones.db"),
            ttl_sesion_segundos=int(os.environ.get("TTL_SESION_SEGUNDOS", "3600")),
        )
