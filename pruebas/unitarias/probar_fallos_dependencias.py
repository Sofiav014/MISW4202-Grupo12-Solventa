import io
import json

import pytest
from flask import Flask

from gateway.auditoria import RegistradorAuditoriaJson
from gateway.clientes.detector import ClienteDetector
from gateway.clientes.identidad import ClienteIdentidad
from gateway.clientes.journey import ClienteHttpJourney
from gateway.clientes.transporte import FalloTransporte, RespuestaHttpCruda
from gateway.modelos import ContextoSesion, SolicitudProtegida
from gateway.politica import PolicyGate
from gateway.respuestas import AdaptadorRespuesta


class TransporteFalso:
    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.llamadas = []

    def solicitar(self, metodo, url, **kwargs):
        self.llamadas.append((metodo, url))
        return self.respuesta


CONTEXTO = ContextoSesion("sesion", "dispositivo", 1.0, 2.0, __import__("datetime").datetime(2026, 1, 1))


@pytest.mark.parametrize("respuesta,categoria", [
    (FalloTransporte("tiempo_espera", 201), "tiempo_espera"),
    (FalloTransporte("conexion", 4), "conexion"),
    (RespuestaHttpCruda(500, {}, b"failure", 3), "contrato_invalido"),
    (RespuestaHttpCruda(200, {}, b"not-json", 3), "contrato_invalido"),
    (RespuestaHttpCruda(200, {}, b'{"veredicto":"otro","motivo":null,"score_riesgo":0.2}', 3), "contrato_invalido"),
    (RespuestaHttpCruda(200, {}, b'["not-an-object"]', 3), "contrato_invalido"),
    (RespuestaHttpCruda(200, {}, b'{"veredicto":"normal","motivo":null,"score_riesgo":Infinity}', 3), "contrato_invalido"),
    (RespuestaHttpCruda(200, {}, b'{"veredicto":"normal","motivo":null,"score_riesgo":0.2}', 201), "presupuesto_excedido"),
])
def test_detector_mapea_todas_las_fallas_y_no_produce_veredicto(respuesta, categoria):
    resultado = ClienteDetector(TransporteFalso(respuesta), "http://detector").evaluar(CONTEXTO, "gateway-id")
    assert resultado.veredicto is None
    assert resultado.fallo.categoria == categoria


@pytest.mark.parametrize("respuesta", [
    FalloTransporte("tiempo_espera", 1),
    FalloTransporte("conexion", 1),
    RespuestaHttpCruda(503, {}, b"failure", 1),
    RespuestaHttpCruda(200, {}, b"not-json", 1),
    RespuestaHttpCruda(200, {}, b'{"status":"pending"}', 1),
    RespuestaHttpCruda(200, {}, b'{"status":"pending","reference":" "}', 1),
    RespuestaHttpCruda(200, {}, b'{"status":"rejected","reference":"ref"}', 1),
])
def test_identidad_requiere_confirmacion_valida(respuesta):
    resultado = ClienteIdentidad(TransporteFalso(respuesta), "http://identidad").iniciar_verificacion("sesion", "gateway-id")
    assert resultado.confirmacion is None
    assert resultado.fallo.categoria in {"tiempo_espera", "conexion", "contrato_invalido"}


@pytest.mark.parametrize("fallo,estado", [("tiempo_espera", 504), ("conexion", 502)])
def test_journey_mapea_disponibilidad(fallo, estado):
    transporte = TransporteFalso(FalloTransporte(fallo, 2))
    resultado = ClienteHttpJourney(transporte, "http://journey").reenviar(
        SolicitudProtegida("GET", "recurso", "", {}, b"", "gateway-id", None))
    assert resultado.respuesta is None
    assert resultado.fallo.categoria == fallo
    assert (504 if fallo == "tiempo_espera" else 502) == estado


def test_journey_propaga_status_inesperado_sin_perder_cuerpo():
    resultado = ClienteHttpJourney(TransporteFalso(RespuestaHttpCruda(418, {}, b"teapot", 2)), "http://journey").reenviar(
        SolicitudProtegida("GET", "recurso", "", {}, b"", "gateway-id", None))
    assert resultado.respuesta.estado == 418
    assert resultado.respuesta.cuerpo == b"teapot"


def test_politica_emite_auditoria_con_id_y_latencia_total_sin_exponer_evidencia():
    salida = io.StringIO()
    auditoria = RegistradorAuditoriaJson(salida)

    class Detector:
        def evaluar(self, *args, **kwargs):
            from gateway.modelos import ResultadoDetector, VeredictoDetector
            return ResultadoDetector(VeredictoDetector("normal", "ubicacion", .91), None, 12)

    class Journey:
        def reenviar(self, solicitud):
            from gateway.modelos import RespuestaJourney, ResultadoJourney
            return ResultadoJourney(RespuestaJourney(200, b"ok", {}))

    class Validador:
        def validar(self, headers): return None

    class Contexto:
        def analizar(self, headers): return CONTEXTO

    resultado = PolicyGate(Validador(), Contexto(), Detector(), Journey(), auditoria=auditoria).manejar(
        SolicitudProtegida("GET", "x", "", {}, b"", "authoritative-id", None))
    eventos = [json.loads(line) for line in salida.getvalue().splitlines()]
    assert resultado.estado == 200
    assert len(eventos) == 1
    assert eventos[0]["request_id"] == "authoritative-id"
    assert eventos[0]["latencia_detector_ms"] == 12
    assert eventos[0]["latencia_pasarela_ms"] >= 0
    assert "score_riesgo" not in b"ok".decode()


@pytest.mark.parametrize("categoria,estado", [
    ("tiempo_espera", 503), ("conexion", 503),
    ("contrato_invalido", 503), ("presupuesto_excedido", 503),
])
def test_politica_detector_falla_es_503_y_no_llama_ramas(categoria, estado):
    llamadas = []

    class Detector:
        def evaluar(self, *args, **kwargs):
            from gateway.modelos import FalloDependencia, ResultadoDetector
            llamadas.append("detector")
            return ResultadoDetector(fallo=FalloDependencia("detector", categoria, 201))

    class Downstream:
        def reenviar(self, *args): llamadas.append("journey")
        def iniciar_verificacion(self, *args, **kwargs): llamadas.append("identidad")

    class Validador:
        def validar(self, headers): return None

    class Contexto:
        def analizar(self, headers): return CONTEXTO

    resultado = PolicyGate(Validador(), Contexto(), Detector(), Downstream(), Downstream()).manejar(
        SolicitudProtegida("GET", "x", "", {}, b"", "gateway-id", None))
    with Flask(__name__).app_context():
        response = AdaptadorRespuesta().a_http(resultado)
        body = response[0].get_json()
    assert response[1] == estado
    assert body == {"error_code": "detector_unavailable", "message": "The security decision is temporarily unavailable.", "request_id": "gateway-id"}
    assert llamadas == ["detector"]


@pytest.mark.parametrize("veredicto,fallo,esperado", [("sospechoso", "identidad", "identity_unavailable"), ("normal", "journey", "journey_unavailable")])
def test_auditoria_decision_se_emite_aunque_falle_la_siguiente_dependencia(veredicto, fallo, esperado):
    salida = io.StringIO()
    auditoria = RegistradorAuditoriaJson(salida)

    class Detector:
        def evaluar(self, *args, **kwargs):
            from gateway.modelos import ResultadoDetector, VeredictoDetector
            return ResultadoDetector(VeredictoDetector(veredicto, "ubicacion", .91), None, 7)

    class Identidad:
        def iniciar_verificacion(self, *args, **kwargs):
            from gateway.modelos import FalloDependencia, ResultadoIdentidad
            return ResultadoIdentidad(fallo=FalloDependencia("identidad", "conexion", 8, "connection_failure"))

    class Journey:
        def reenviar(self, *args):
            from gateway.modelos import FalloDependencia, ResultadoJourney
            return ResultadoJourney(fallo=FalloDependencia("journey", "tiempo_espera", 9, "timeout"))

    class V:
        def validar(self, headers): return None
    class C:
        def analizar(self, headers): return CONTEXTO

    puerta = PolicyGate(V(), C(), Detector(), Journey(), Identidad(), auditoria)
    resultado = puerta.manejar(SolicitudProtegida("GET", "x", "", {}, b"", "request-123", None))
    eventos = [json.loads(line) for line in salida.getvalue().splitlines()]
    assert resultado.codigo_error == esperado
    assert {evento["evento"] for evento in eventos} == {"decision_politica", "disponibilidad"}
    assert all(evento["request_id"] == "request-123" for evento in eventos)
    assert all(evento.get("causa") != "" for evento in eventos if evento["evento"] == "disponibilidad")
    assert eventos[-1]["latencia_pasarela_ms"] >= eventos[-1]["latencia_detector_ms"]
