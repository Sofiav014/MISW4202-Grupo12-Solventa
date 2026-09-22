import json
import jwt

from gateway.modelos import SolicitudProtegida
from gateway.politica import PolicyGate
from gateway.validacion import AnalizadorContextoSesion, ValidadorPyJwt


def solicitud(token=None, contexto=True):
    cabeceras = {"Authorization": f"Bearer {token}" if token else ""}
    if contexto:
        cabeceras["X-Session-Context"] = json.dumps({"session_id": "s", "device_id": "d", "geo_lat": 0, "geo_lon": 0, "timestamp": "2026-01-01T00:00:00+00:00"})
    return SolicitudProtegida("GET", "x", "", cabeceras, b"", "gateway-id", "client-id")


def test_jwt_se_valida_antes_del_contexto():
    token = jwt.encode({"sub": "u"}, "secret", algorithm="HS256")
    resultado = PolicyGate(ValidadorPyJwt("secret"), AnalizadorContextoSesion()).manejar(solicitud(token, contexto=False))
    assert resultado.codigo_error == "invalid_session_context"
    assert resultado.estado_http == 400


def test_jwt_invalido_detiene_la_puerta():
    resultado = PolicyGate(ValidadorPyJwt("secret"), AnalizadorContextoSesion()).manejar(solicitud("bad"))
    assert resultado.codigo_error == "auth_invalid"
    assert resultado.estado_http == 401


def test_sospechoso_activa_identidad_y_bloquea_journey():
    eventos = []
    class Detector:
        def evaluar(self, contexto, id_solicitud, escenario=None):
            eventos.append("detector")
            from gateway.modelos import ResultadoDetector, VeredictoDetector
            return ResultadoDetector(veredicto=VeredictoDetector("sospechoso", "ubicacion", .9))
    class Identidad:
        def iniciar_verificacion(self, id_sesion, id_solicitud, escenario=None):
            eventos.append("identidad")
            from gateway.modelos import ConfirmacionIdentidad, ResultadoIdentidad
            return ResultadoIdentidad(confirmacion=ConfirmacionIdentidad("pending", "ref"))
    class Journey:
        def reenviar(self, solicitud):
            eventos.append("journey")
    token = jwt.encode({"sub": "u"}, "secret", algorithm="HS256")
    resultado = PolicyGate(ValidadorPyJwt("secret"), AnalizadorContextoSesion(), Detector(), Journey(), Identidad()).manejar(solicitud(token))
    assert (resultado.codigo_error, resultado.estado_http) == ("verification_required", 403)
    assert eventos == ["detector", "identidad"]


def test_detector_falla_cierra_y_no_llama_dependencias():
    eventos = []
    class Detector:
        def evaluar(self, *args, **kwargs):
            eventos.append("detector")
            from gateway.modelos import ResultadoDetector, FalloDependencia
            return ResultadoDetector(fallo=FalloDependencia("detector", "presupuesto_excedido", 201))
    class Downstream:
        def reenviar(self, *args): eventos.append("journey")
        def iniciar_verificacion(self, *args, **kwargs): eventos.append("identidad")
    token = jwt.encode({"sub": "u"}, "secret", algorithm="HS256")
    resultado = PolicyGate(ValidadorPyJwt("secret"), AnalizadorContextoSesion(), Detector(), Downstream(), Downstream()).manejar(solicitud(token))
    assert (resultado.codigo_error, resultado.estado_http) == ("detector_unavailable", 503)
    assert eventos == ["detector"]


def test_excepcion_global_es_envolvente_segura():
    from gateway.aplicacion import crear_aplicacion
    from gateway.configuracion import ConfiguracionPasarela
    class Explota:
        def manejar(self, solicitud): raise RuntimeError("secret cause")
    app = crear_aplicacion(ConfiguracionPasarela("secret"), orquestador=Explota())
    token = jwt.encode({"sub": "u"}, "secret", algorithm="HS256")
    contexto = json.dumps({"session_id":"s", "device_id":"d", "geo_lat":0, "geo_lon":0, "timestamp":"2026-01-01T00:00:00+00:00"})
    response = app.test_client().get("/api/journey/x", headers={"Authorization":f"Bearer {token}", "X-Session-Context":contexto})
    assert response.status_code == 500
    assert response.get_json()["error_code"] == "gateway_internal_error"
    assert "secret cause" not in response.get_data(as_text=True)
