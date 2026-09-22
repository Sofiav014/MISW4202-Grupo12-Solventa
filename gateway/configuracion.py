from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ConfiguracionPasarela:
    secreto_jwt: str
    url_detector: str = "http://localhost:8001"
    url_identidad: str = "http://localhost:8002"
    url_journey: str = "http://localhost:8003"
    tiempo_espera_detector_ms: int = 200
    tiempo_espera_identidad_ms: int = 500
    tiempo_espera_journey_ms: int = 500
    prefijo_journey: str = "/api/journey"
    algoritmo_jwt: str = "HS256"
    permitir_escenario_stub: bool = False

    @classmethod
    def desde_entorno(cls) -> "ConfiguracionPasarela":
        return cls(
            secreto_jwt=os.environ["SECRETO_JWT"],
            url_detector=os.environ.get("URL_DETECTOR", "http://localhost:8001"),
            url_identidad=os.environ.get("URL_IDENTIDAD", "http://localhost:8002"),
            url_journey=os.environ.get("URL_JOURNEY", "http://localhost:8003"),
            tiempo_espera_detector_ms=int(os.environ.get("TIEMPO_ESPERA_DETECTOR_MS", "200")),
            tiempo_espera_identidad_ms=int(os.environ.get("TIEMPO_ESPERA_IDENTIDAD_MS", "500")),
            tiempo_espera_journey_ms=int(os.environ.get("TIEMPO_ESPERA_JOURNEY_MS", "500")),
            prefijo_journey=os.environ.get("PREFIJO_JOURNEY", "/api/journey"),
            algoritmo_jwt=os.environ.get("ALGORITMO_JWT", "HS256"),
            permitir_escenario_stub=os.environ.get("PERMITIR_ESCENARIO_STUB", "false").lower() == "true",
        )
