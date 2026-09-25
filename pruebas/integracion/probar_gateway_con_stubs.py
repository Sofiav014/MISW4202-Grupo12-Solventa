import json

import jwt

from gateway.aplicacion import crear_aplicacion
from gateway.configuracion import ConfiguracionPasarela
from dobles_http.detector_prueba import crear_aplicacion as crear_detector
from dobles_http.journey_prueba import crear_aplicacion as crear_journey
from dobles_http.identidad_prueba import crear_aplicacion as crear_identidad


class TransporteFlask:
    def __init__(self, cliente):
        self.cliente = cliente

    def solicitar(self, metodo, url, *, cabeceras=None, cuerpo=b"", tiempo_espera_ms=500):
        from gateway.clientes.transporte import RespuestaHttpCruda
        from urllib.parse import urlsplit
        partes = urlsplit(url)
        respuesta = self.cliente.open(path=partes.path + (("?" + partes.query) if partes.query else ""), method=metodo,
                                      headers=cabeceras or {}, data=cuerpo)
        return RespuestaHttpCruda(respuesta.status_code, dict(respuesta.headers), respuesta.data, 1.0)


def test_normal_proxy_usa_contexto_y_preserva_solicitud():
    detector = crear_detector().test_client()
    journey = crear_journey().test_client()
    from gateway.clientes.detector import ClienteDetector
    from gateway.clientes.journey import ClienteHttpJourney
    from gateway.politica import PolicyGate
    from gateway.validacion import AnalizadorContextoSesion, ValidadorPyJwt

    puerta = PolicyGate(ValidadorPyJwt("secret"), AnalizadorContextoSesion(),
                        ClienteDetector(TransporteFlask(detector), "http://detector", permitir_escenario_stub=True),
                        ClienteHttpJourney(TransporteFlask(journey), "http://journey", permitir_escenario_stub=True))
    aplicacion = crear_aplicacion(ConfiguracionPasarela("secret"), orquestador=puerta)
    token = jwt.encode({"sub": "user"}, "secret", algorithm="HS256")
    contexto = json.dumps({"session_id": "s", "device_id": "d", "geo_lat": 1, "geo_lon": 2,
                           "timestamp": "2026-01-01T00:00:00+00:00"})
    respuesta = aplicacion.test_client().post("/api/journey/orders?source=test", data=b"payload",
        headers={"Authorization": f"Bearer {token}", "X-Session-Context": contexto,
                 "X-Request-ID": "client-id", "X-Escenario-Stub": "normal", "Content-Type": "text/plain"})
    assert respuesta.status_code == 201
    assert respuesta.get_json() == {"journey": "ok"}
    assert "score_riesgo" not in respuesta.get_data(as_text=True)
    llamada = journey.application.llamadas[0]
    assert (llamada["metodo"], llamada["ruta"], llamada["consulta"], llamada["cuerpo"]) == ("POST", "orders", "source=test", b"payload")
    assert "X-Session-Context" not in llamada["cabeceras"]
    assert "X-Escenario-Stub" not in llamada["cabeceras"]
    assert llamada["cabeceras"]["Request-Id"]


def test_sospechoso_confirma_identidad_y_no_llama_journey():
    detector = crear_detector().test_client()
    identidad = crear_identidad().test_client()
    journey = crear_journey().test_client()
    from gateway.clientes.detector import ClienteDetector
    from gateway.clientes.identidad import ClienteIdentidad
    from gateway.clientes.journey import ClienteHttpJourney
    from gateway.politica import PolicyGate
    from gateway.validacion import AnalizadorContextoSesion, ValidadorPyJwt
    puerta = PolicyGate(ValidadorPyJwt("secret"), AnalizadorContextoSesion(),
                        ClienteDetector(TransporteFlask(detector), "http://detector", permitir_escenario_stub=True),
                        ClienteHttpJourney(TransporteFlask(journey), "http://journey", permitir_escenario_stub=True),
                        ClienteIdentidad(TransporteFlask(identidad), "http://identidad", permitir_escenario_stub=True))
    aplicacion = crear_aplicacion(ConfiguracionPasarela("secret"), orquestador=puerta)
    token = jwt.encode({"sub": "user"}, "secret", algorithm="HS256")
    contexto = json.dumps({"session_id": "s", "device_id": "d", "geo_lat": 1, "geo_lon": 2, "timestamp": "2026-01-01T00:00:00+00:00"})
    respuesta = aplicacion.test_client().get("/api/journey/orders", headers={"Authorization": f"Bearer {token}", "X-Session-Context": contexto, "X-Escenario-Stub": "sospechoso"})
    assert respuesta.status_code == 403
    assert respuesta.get_json()["error_code"] == "verification_required"
    assert len(identidad.application.llamadas) == 1
    assert not journey.application.llamadas


def test_sospechoso_con_identidad_no_confirmada_es_indisponible():
    detector, identidad, journey = crear_detector().test_client(), crear_identidad().test_client(), crear_journey().test_client()
    from gateway.clientes.detector import ClienteDetector
    from gateway.clientes.identidad import ClienteIdentidad
    from gateway.clientes.journey import ClienteHttpJourney
    from gateway.politica import PolicyGate
    from gateway.validacion import AnalizadorContextoSesion, ValidadorPyJwt
    puerta = PolicyGate(ValidadorPyJwt("secret"), AnalizadorContextoSesion(), ClienteDetector(TransporteFlask(detector), "http://detector", permitir_escenario_stub=True), ClienteHttpJourney(TransporteFlask(journey), "http://journey", permitir_escenario_stub=True), ClienteIdentidad(TransporteFlask(identidad), "http://identidad", permitir_escenario_stub=True))
    aplicacion = crear_aplicacion(ConfiguracionPasarela("secret"), orquestador=puerta)
    token = jwt.encode({"sub": "user"}, "secret", algorithm="HS256")
    contexto = json.dumps({"session_id": "s", "device_id": "d", "geo_lat": 1, "geo_lon": 2, "timestamp": "2026-01-01T00:00:00+00:00"})
    respuesta = aplicacion.test_client().get("/api/journey/orders", headers={"Authorization": f"Bearer {token}", "X-Session-Context": contexto, "X-Escenario-Stub": "sospechoso,identidad-falla"})
    assert respuesta.status_code == 503
    assert respuesta.get_json()["error_code"] == "identity_unavailable"
    assert not journey.application.llamadas
