"""
Construcción de las peticiones del experimento, con su verdad-terreno.

Cada muestra sabe qué debería decidir el sistema sobre ella. Esa etiqueta es
lo que permite calcular después la matriz de confusión: el código de respuesta
por sí solo no distingue un 403 acertado sobre un ataque de un 403 equivocado
sobre un cliente legítimo, porque ambos son el mismo 403.

El módulo es puro —ni HTTP ni locust— para poder verificar con pruebas que el
desplazamiento geográfico que produce coincide con el que mide el Detector.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import pi
import uuid

from deteccion.geo import RADIO_TIERRA_KM


# Etiquetas de verdad-terreno: qué es realmente la petición.
ETIQUETA_LEGITIMA = "legitima"
ETIQUETA_SUPLANTADA = "suplantada"

# Modos de construcción. El modo describe *cómo* se fabricó la petición; la
# etiqueta, qué debería decidirse sobre ella. Se separan porque la frontera del
# radio produce ambas etiquetas según la distancia sorteada.
MODO_LEGITIMO = "legitimo"
MODO_DISPOSITIVO = "suplantado_dispositivo"
MODO_UBICACION = "suplantado_ubicacion"
MODO_FRONTERA = "frontera_radio"

# Kilómetros por grado de latitud sobre el meridiano. Se deriva del mismo radio
# terrestre que usa `deteccion.geo` para que desplazar y medir sean operaciones
# inversas exactas; codificar un 111 aproximado introduciría un error de varios
# cientos de metros, suficiente para que un punto pensado "a 49 km" cayera al
# otro lado de un umbral de 50.
KM_POR_GRADO_LATITUD = (pi / 180) * RADIO_TIERRA_KM

# Dispersión del tráfico legítimo alrededor de la ubicación registrada. Se
# mantiene diminuta a propósito: el Detector reescribe la línea base en cada
# veredicto normal, así que un jitter grande se acumularía a lo largo de la
# corrida hasta acercarse al radio y generar falsos positivos que serían
# artefacto del generador, no del sistema.
JITTER_LEGITIMO_GRADOS = 0.01


@dataclass(frozen=True)
class Muestra:
    """
    Una petición lista para enviar, junto con lo que debería decidirse sobre ella.

    Attributes:
        session_id: Sesión a la que pertenece la petición.
        token: JWT emitido por Identidad para esa sesión.
        contexto: Los cinco campos exactos que exige `X-Session-Context`.
        etiqueta_verdad: `legitima` o `suplantada`; la verdad-terreno.
        modo: Cómo se construyó la muestra.
        distancia_km: Desplazamiento aplicado, o `None` si no se movió.
        device_alterado: Si el dispositivo declarado no es el registrado.
    """

    session_id: str
    token: str
    contexto: dict
    etiqueta_verdad: str
    modo: str
    distancia_km: float | None
    device_alterado: bool


def desplazar(lat: float, lon: float, distancia_km: float) -> tuple[float, float]:
    """
    Mueve un punto la distancia indicada hacia el norte.

    Se desplaza solo en latitud porque sobre el meridiano la conversión entre
    grados y kilómetros es exacta y no depende de la latitud de partida. Un
    desplazamiento en longitud tendría que corregirse por el coseno de la
    latitud, y el error residual bastaría para desordenar los puntos de la
    barrida alrededor del radio.

    Args:
        lat: Latitud de origen, en grados decimales.
        lon: Longitud de origen, en grados decimales.
        distancia_km: Distancia a recorrer, en kilómetros.

    Returns:
        Las coordenadas desplazadas.
    """
    destino = lat + distancia_km / KM_POR_GRADO_LATITUD

    # Cerca de los polos el desplazamiento al norte se saldría del rango que el
    # Gateway acepta; se refleja hacia el sur para conservar la distancia. La
    # población del experimento está en Colombia, así que es una salvaguarda.
    if destino > 90:
        destino = lat - distancia_km / KM_POR_GRADO_LATITUD

    return destino, lon


def _ahora_iso() -> str:
    """
    Devuelve el instante actual en ISO 8601 con zona horaria.

    El Gateway rechaza con 400 cualquier timestamp sin zona explícita, de modo
    que `utcnow()` —que produce un datetime ingenuo— haría fallar toda la
    corrida por un motivo ajeno a la detección.
    """
    return datetime.now(timezone.utc).isoformat()


def _contexto(session_id: str, device_id: str, lat: float, lon: float) -> dict:
    """Arma el contexto de sesión con los cinco campos que exige el Gateway."""
    return {
        "session_id": session_id,
        "device_id": device_id,
        "geo_lat": lat,
        "geo_lon": lon,
        "timestamp": _ahora_iso(),
    }


def construir(credencial, modo: str, aleatorio, *, distancia_km: float | None = None) -> Muestra:
    """
    Construye una petición del modo indicado a partir de una credencial sembrada.

    Los modos suplantados alteran **una sola** señal cada uno. El Detector
    evalúa el dispositivo antes que la ubicación y devuelve el primer motivo que
    encuentra, así que una muestra con ambas señales alteradas se detectaría
    siempre por dispositivo y el escenario de ubicación no mediría su regla.

    En todos los casos se conserva el JWT legítimo de la sesión: el ataque que
    describe HA16 es el de una sesión válida usada desde un contexto que no
    corresponde. Un token inválido produciría un 401 del Gateway, que no es una
    decisión del Detector.

    Args:
        credencial: Credencial sembrada de la que se toma sesión, token y
            contexto registrado.
        modo: Uno de los `MODO_*`.
        aleatorio: Generador pseudoaleatorio con semilla fija.
        distancia_km: Desplazamiento a aplicar. Obligatorio en los modos que
            mueven la ubicación.

    Returns:
        La muestra construida, con su etiqueta de verdad.

    Raises:
        ValueError: Si el modo es desconocido o falta la distancia que requiere.
    """
    if modo == MODO_LEGITIMO:
        # Ruido pequeño alrededor del punto registrado, para que el tráfico
        # legítimo no sea una repetición idéntica del mismo par de coordenadas.
        #
        # El Detector reescribe la última ubicación conocida en cada veredicto
        # normal, así que este ruido desplaza la referencia de la sesión. Por eso
        # los escenarios de frontera reservan sesiones que no reciben tráfico
        # legítimo (ver `escenarios.reservar_frontera`): se midió que una deriva
        # de 1.2 km bastaba para que una petición construida a 50 km quedara a
        # 51.1 km de su referencia y se contara como falso positivo del sistema
        # cuando era un artefacto del generador.
        lat = credencial.lat + aleatorio.uniform(-JITTER_LEGITIMO_GRADOS, JITTER_LEGITIMO_GRADOS)
        lon = credencial.lon + aleatorio.uniform(-JITTER_LEGITIMO_GRADOS, JITTER_LEGITIMO_GRADOS)

        return Muestra(
            session_id=credencial.session_id,
            token=credencial.token,
            contexto=_contexto(credencial.session_id, credencial.device_id, lat, lon),
            etiqueta_verdad=ETIQUETA_LEGITIMA,
            modo=modo,
            distancia_km=None,
            device_alterado=False,
        )

    if modo == MODO_DISPOSITIVO:
        # Ubicación legítima; solo cambia el dispositivo. El sufijo aleatorio
        # evita que el Detector pudiera reconocer un valor repetido.
        return Muestra(
            session_id=credencial.session_id,
            token=credencial.token,
            contexto=_contexto(
                credencial.session_id,
                f"dispositivo-suplantado-{uuid.uuid4().hex[:8]}",
                credencial.lat,
                credencial.lon,
            ),
            etiqueta_verdad=ETIQUETA_SUPLANTADA,
            modo=modo,
            distancia_km=None,
            device_alterado=True,
        )

    if modo in (MODO_UBICACION, MODO_FRONTERA):
        if distancia_km is None:
            raise ValueError(f"El modo {modo} requiere distancia_km.")

        lat, lon = desplazar(credencial.lat, credencial.lon, distancia_km)

        return Muestra(
            session_id=credencial.session_id,
            token=credencial.token,
            contexto=_contexto(credencial.session_id, credencial.device_id, lat, lon),
            # En la barrida de frontera la etiqueta depende de la distancia: un
            # punto dentro del radio es un cliente legítimo que se movió, y
            # marcarlo como ataque invertiría el signo de los falsos positivos.
            etiqueta_verdad=etiqueta_por_distancia(distancia_km),
            modo=modo,
            distancia_km=distancia_km,
            device_alterado=False,
        )

    raise ValueError(f"Modo desconocido: {modo!r}")


def etiqueta_por_distancia(distancia_km: float, radio_km: float | None = None) -> str:
    """
    Decide la verdad-terreno de un desplazamiento según el radio acordado.

    Args:
        distancia_km: Distancia desde la ubicación registrada.
        radio_km: Radio a aplicar; por defecto el parámetro del experimento.

    Returns:
        `ETIQUETA_SUPLANTADA` si el punto queda fuera del radio, en cuyo caso el
        sistema debería suspender la operación; `ETIQUETA_LEGITIMA` si no.
    """
    if radio_km is None:
        from parametros import RADIO_UBICACION_KM

        radio_km = RADIO_UBICACION_KM

    return ETIQUETA_SUPLANTADA if distancia_km > radio_km else ETIQUETA_LEGITIMA
