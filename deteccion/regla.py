from datetime import timedelta

from .geo import distancia_km
from .modelos import (
    MOTIVO_DISPOSITIVO,
    MOTIVO_UBICACION,
    VEREDICTO_NORMAL,
    VEREDICTO_SOSPECHOSO,
    ContextoEvaluacion,
    LineaBase,
    Veredicto,
)


# Puntajes asociados a cada desenlace. Son deliberadamente estables: el
# experimento contrasta la decisión, no la calibración fina del puntaje.
SCORE_NORMAL = 0.05
SCORE_SIN_LINEA_BASE = 0.85
SCORE_DISPOSITIVO = 0.95
SCORE_UBICACION_BASE = 0.70
SCORE_MAXIMO = 0.99


class ReglaDecision:
    """
    Regla de decisión de contexto del Detector.

    Contrasta la solicitud entrante con la línea base del cliente y produce
    uno de los tres desenlaces previstos en el diseño del experimento:

    - dispositivo distinto del registrado -> sospechoso (motivo dispositivo);
    - ubicación fuera del radio respecto a la actividad reciente -> sospechoso
      (motivo ubicación);
    - ambos coinciden -> normal.
    """

    def __init__(self, radio_ubicacion_km: float, ventana_actividad_min: int):
        """
        Args:
            radio_ubicacion_km:
                Distancia máxima aceptable frente a la última ubicación
                conocida antes de considerar la ubicación incompatible.

            ventana_actividad_min:
                Antigüedad máxima de la última ubicación conocida para que
                siga siendo comparable.
        """
        self.radio_ubicacion_km = radio_ubicacion_km
        self.ventana_actividad_min = ventana_actividad_min

    def evaluar(self, contexto: ContextoEvaluacion, linea_base: LineaBase) -> Veredicto:
        """
        Produce el veredicto para una solicitud.

        Args:
            contexto:
                Datos de la solicitud entrante.

            linea_base:
                Referencia conocida del cliente.

        Returns:
            El ``Veredicto`` correspondiente.
        """
        # Sin dispositivo registrado no hay nada contra qué contrastar. Dejar
        # pasar la solicitud equivaldría a que basta con presentar una sesión
        # desconocida para eludir la regla de dispositivo, así que se resuelve
        # en el sentido conservador.
        if not linea_base.device_id_registrado:
            return Veredicto(VEREDICTO_SOSPECHOSO, MOTIVO_DISPOSITIVO, SCORE_SIN_LINEA_BASE)

        if contexto.device_id != linea_base.device_id_registrado:
            return Veredicto(VEREDICTO_SOSPECHOSO, MOTIVO_DISPOSITIVO, SCORE_DISPOSITIVO)

        distancia = self._distancia_si_comparable(contexto, linea_base)

        if distancia is not None and distancia > self.radio_ubicacion_km:
            return Veredicto(
                VEREDICTO_SOSPECHOSO,
                MOTIVO_UBICACION,
                self._score_ubicacion(distancia),
            )

        return Veredicto(VEREDICTO_NORMAL, None, SCORE_NORMAL)

    def _distancia_si_comparable(
        self,
        contexto: ContextoEvaluacion,
        linea_base: LineaBase,
    ) -> float | None:
        """
        Calcula la distancia a la última ubicación conocida, si procede.

        Devuelve ``None`` cuando no hay ubicación previa o cuando esta quedó
        fuera de la ventana de actividad reciente: pasado ese tiempo el
        cliente pudo desplazarse legítimamente y aplicar la regla produciría
        falsos positivos.
        """
        if linea_base.ultima_lat is None or linea_base.ultima_lon is None:
            return None

        if linea_base.ultima_actividad is None:
            return None

        antiguedad = contexto.instante - linea_base.ultima_actividad

        if antiguedad > timedelta(minutes=self.ventana_actividad_min):
            return None

        return distancia_km(
            contexto.geo_lat,
            contexto.geo_lon,
            linea_base.ultima_lat,
            linea_base.ultima_lon,
        )

    def _score_ubicacion(self, distancia: float) -> float:
        """
        Gradúa el puntaje según cuánto se excede el radio permitido.

        Un salto de miles de kilómetros es más concluyente que uno que apenas
        rebasa el umbral, y el puntaje lo refleja sin llegar nunca a 1.
        """
        exceso = (distancia - self.radio_ubicacion_km) / max(self.radio_ubicacion_km, 1.0)
        return round(min(SCORE_UBICACION_BASE + exceso * 0.1, SCORE_MAXIMO), 4)
