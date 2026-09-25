from gateway.modelos import ErrorPasarela
from gateway.respuestas import AdaptadorRespuesta
from flask import Flask


def test_error_usa_sobre_seguro():
    with Flask(__name__).app_context():
        respuesta, estado = AdaptadorRespuesta().a_http(
            ErrorPasarela("invalid_session_context", 400, "safe", "abc")
        )
    assert estado == 400
    assert respuesta.get_json() == {"error_code": "invalid_session_context", "message": "safe", "request_id": "abc"}
