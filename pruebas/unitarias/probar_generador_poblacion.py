import random

import pytest

from deteccion.geo import distancia_km
from deteccion.semilla import CIUDADES
from generador.poblacion import (
    ETIQUETA_LEGITIMA,
    ETIQUETA_SUPLANTADA,
    MODO_DISPOSITIVO,
    MODO_FRONTERA,
    MODO_LEGITIMO,
    MODO_UBICACION,
    construir,
    desplazar,
    etiqueta_por_distancia,
)
from generador.registro import (
    OBSERVADO_ENRUTADA,
    OBSERVADO_ERROR_INFRA,
    OBSERVADO_RECHAZADA,
    OBSERVADO_SUSPENDIDA,
    clasificar,
)
from generador.sembrar_identidad import Credencial


# Distancias alrededor del radio acordado de 50 km: interesan sobre todo las
# que lo rozan, porque son las que deciden si la barrida de frontera puede
# discriminar a un lado y otro del umbral.
DISTANCIAS = (10, 45, 48, 49, 50, 51, 52, 55, 100)

# Margen tolerado entre la distancia pedida y la que mide el Detector. Es una
# décima parte de la separación entre los puntos más juntos de la barrida.
TOLERANCIA_KM = 0.05


@pytest.fixture
def credencial():
    """Credencial de referencia, situada en Bogotá."""
    return Credencial(
        session_id="sesion-00000",
        cliente_id="cliente-00000",
        device_id="dispositivo-bogota-00000",
        lat=4.7110,
        lon=-74.0721,
        token="token-de-prueba",
        expira_en=None,
    )


@pytest.mark.parametrize("ciudad", CIUDADES, ids=lambda c: c[0])
@pytest.mark.parametrize("distancia", DISTANCIAS)
def test_desplazar_es_el_inverso_de_la_distancia_que_mide_el_detector(ciudad, distancia):
    """Mover un punto N km y medirlo con la fórmula del Detector devuelve N km.

    Es la propiedad de la que depende toda la barrida de frontera: si el
    generador y el Detector no coincidieran en cuánto es un kilómetro, un punto
    construido "a 49 km" podría caer del otro lado del umbral y contarse como
    una detección errónea que en realidad es un fallo de instrumentación.
    """
    _, lat, lon = ciudad
    medida = distancia_km(lat, lon, *desplazar(lat, lon, distancia))

    assert abs(medida - distancia) < TOLERANCIA_KM


def test_contexto_legitimo_conserva_dispositivo_y_no_se_aleja(credencial):
    """El tráfico legítimo mantiene el dispositivo registrado y apenas se mueve."""
    muestra = construir(credencial, MODO_LEGITIMO, random.Random(1))

    assert muestra.etiqueta_verdad == ETIQUETA_LEGITIMA
    assert muestra.device_alterado is False
    assert muestra.contexto["device_id"] == credencial.device_id

    desviacion = distancia_km(
        credencial.lat, credencial.lon,
        muestra.contexto["geo_lat"], muestra.contexto["geo_lon"],
    )
    # El jitter debe quedar muy por debajo del radio; si creciera, el arrastre
    # de la línea base acabaría produciendo falsos positivos del generador.
    assert desviacion < 2.0


def test_suplantacion_por_dispositivo_no_mueve_la_ubicacion(credencial):
    """Alterar el dispositivo deja la ubicación intacta, para aislar la causa."""
    muestra = construir(credencial, MODO_DISPOSITIVO, random.Random(1))

    assert muestra.etiqueta_verdad == ETIQUETA_SUPLANTADA
    assert muestra.device_alterado is True
    assert muestra.contexto["device_id"] != credencial.device_id
    assert muestra.contexto["geo_lat"] == credencial.lat
    assert muestra.contexto["geo_lon"] == credencial.lon


def test_suplantacion_por_ubicacion_no_altera_el_dispositivo(credencial):
    """Desplazar la ubicación conserva el dispositivo registrado.

    El Detector evalúa el dispositivo primero y devuelve el primer motivo que
    encuentra, así que alterar ambas señales haría que este escenario midiera
    la regla equivocada.
    """
    muestra = construir(credencial, MODO_UBICACION, random.Random(1), distancia_km=200)

    assert muestra.etiqueta_verdad == ETIQUETA_SUPLANTADA
    assert muestra.device_alterado is False
    assert muestra.contexto["device_id"] == credencial.device_id
    assert muestra.distancia_km == 200


def test_el_token_legitimo_se_conserva_en_las_suplantaciones(credencial):
    """La suplantación usa la sesión válida de la víctima, como describe HA16.

    Un token inválido produciría un 401 del Gateway, que no es una decisión del
    Detector y no mediría detección alguna.
    """
    for modo, distancia in ((MODO_DISPOSITIVO, None), (MODO_UBICACION, 200)):
        muestra = construir(credencial, modo, random.Random(1), distancia_km=distancia)
        assert muestra.token == credencial.token


def test_la_frontera_etiqueta_segun_el_radio(credencial):
    """Dentro del radio la muestra es legítima; fuera, suplantada."""
    dentro = construir(credencial, MODO_FRONTERA, random.Random(1), distancia_km=49)
    fuera = construir(credencial, MODO_FRONTERA, random.Random(1), distancia_km=51)

    assert dentro.etiqueta_verdad == ETIQUETA_LEGITIMA
    assert fuera.etiqueta_verdad == ETIQUETA_SUPLANTADA


def test_el_radio_exacto_cuenta_como_legitimo():
    """La distancia igual al radio no se considera incompatible.

    El Detector marca sospechoso solo cuando la distancia *supera* el radio, y
    la etiqueta debe usar el mismo criterio para no contabilizar un desacuerdo
    que no existe.
    """
    assert etiqueta_por_distancia(50.0, radio_km=50.0) == ETIQUETA_LEGITIMA
    assert etiqueta_por_distancia(50.001, radio_km=50.0) == ETIQUETA_SUPLANTADA


def test_el_contexto_lleva_exactamente_los_campos_que_exige_el_gateway(credencial):
    """El Gateway rechaza con 400 un contexto con campos de más o de menos."""
    muestra = construir(credencial, MODO_LEGITIMO, random.Random(1))

    assert set(muestra.contexto) == {
        "session_id", "device_id", "geo_lat", "geo_lon", "timestamp",
    }


def test_el_timestamp_declara_zona_horaria(credencial):
    """Un timestamp sin zona horaria haría fallar la corrida entera con 400."""
    from datetime import datetime

    muestra = construir(credencial, MODO_LEGITIMO, random.Random(1))
    instante = datetime.fromisoformat(muestra.contexto["timestamp"])

    assert instante.tzinfo is not None


def test_la_construccion_es_reproducible_con_la_misma_semilla(credencial):
    """Dos generadores con igual semilla producen la misma ubicación."""
    primera = construir(credencial, MODO_LEGITIMO, random.Random(7))
    segunda = construir(credencial, MODO_LEGITIMO, random.Random(7))

    assert primera.contexto["geo_lat"] == segunda.contexto["geo_lat"]
    assert primera.contexto["geo_lon"] == segunda.contexto["geo_lon"]


def test_un_modo_desconocido_falla_de_inmediato(credencial):
    """Un modo mal escrito debe romper al construir, no producir tráfico mudo."""
    with pytest.raises(ValueError):
        construir(credencial, "modo-inexistente", random.Random(1))


def test_los_modos_que_desplazan_exigen_distancia(credencial):
    """Sin distancia no se puede construir una muestra de ubicación."""
    with pytest.raises(ValueError):
        construir(credencial, MODO_UBICACION, random.Random(1))


def test_la_suspension_se_reconoce_por_su_codigo_de_error():
    """Solo el 403 con verification_required cuenta como suspensión de HA16."""
    assert clasificar(403, "verification_required") == OBSERVADO_SUSPENDIDA
    # Un 403 por otra causa no es el desenlace que el experimento persigue.
    assert clasificar(403, "otra_cosa") == OBSERVADO_RECHAZADA


def test_las_respuestas_del_journey_cuentan_como_enrutadas():
    assert clasificar(200, None) == OBSERVADO_ENRUTADA
    assert clasificar(201, None) == OBSERVADO_ENRUTADA


def test_los_fallos_de_infraestructura_se_separan_de_las_decisiones():
    """Un 5xx o una caída de transporte no son juicios sobre la sesión.

    El análisis los excluye del denominador de la matriz de confusión, así que
    deben quedar clasificados aparte y no como aciertos ni como errores.
    """
    for status in (500, 502, 503, 504):
        assert clasificar(status, None) == OBSERVADO_ERROR_INFRA

    assert clasificar(None, None) == OBSERVADO_ERROR_INFRA


def test_las_peticiones_mal_formadas_se_separan_de_las_decisiones():
    """Un 400 o un 401 delatan un error del generador, no una detección."""
    assert clasificar(400, "invalid_session_context") == OBSERVADO_RECHAZADA
    assert clasificar(401, "auth_required") == OBSERVADO_RECHAZADA
