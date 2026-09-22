from flask import jsonify

from .modelos import ErrorPasarela, RespuestaJourney


MENSAJES = {
    "auth_required": "Authentication is required.",
    "auth_invalid": "Authentication is invalid.",
    "invalid_session_context": "The session context is invalid.",
    "detector_unavailable": "The security decision is temporarily unavailable.",
    "journey_unavailable": "The protected service is temporarily unavailable.",
    "verification_required": "Additional verification is required.",
    "gateway_internal_error": "The gateway could not complete the request.",
}


class AdaptadorRespuesta:
    def a_http(self, resultado):
        if isinstance(resultado, RespuestaJourney):
            return resultado.cuerpo, resultado.estado, resultado.cabeceras
        return jsonify(
            error_code=resultado.codigo_error,
            message=resultado.mensaje,
            request_id=resultado.id_solicitud,
        ), resultado.estado_http
