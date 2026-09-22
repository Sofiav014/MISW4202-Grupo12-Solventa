import time
from .modelos import ErrorPasarela, SolicitudProtegida
from .respuestas import MENSAJES


class PolicyGate:
    def __init__(self, validador_jwt, analizador_contexto, cliente_detector=None, cliente_journey=None, cliente_identidad=None, auditoria=None):
        self.validador_jwt = validador_jwt
        self.analizador_contexto = analizador_contexto
        self.cliente_detector, self.cliente_journey, self.cliente_identidad = cliente_detector, cliente_journey, cliente_identidad
        self.auditoria = auditoria

    def _auditar_decision(self, solicitud, resultado, inicio):
        if self.auditoria:
            veredicto = resultado.veredicto
            self.auditoria.registrar("decision_politica", request_id=solicitud.id_solicitud,
                veredicto=veredicto.veredicto, motivo=veredicto.motivo,
                score_riesgo=veredicto.score_riesgo,
                latencia_detector_ms=resultado.latencia_ms,
                latencia_pasarela_ms=max(
                    (time.monotonic() - inicio) * 1000,
                    resultado.latencia_ms or 0,
                ))

def manejar(self, solicitud: SolicitudProtegida) -> ErrorPasarela:
    # Inicio de la medición de tiempo para registrar posteriormente
    # la latencia total de la decisión tomada por la pasarela.
    inicio = time.monotonic()

    # 1. Autenticación: validar el JWT antes de procesar cualquier
    # información de sesión o contactar servicios internos.
    fallo_jwt = self.validador_jwt.validar(solicitud.cabeceras)
    if fallo_jwt:
        return ErrorPasarela(
            fallo_jwt.codigo,
            401,
            MENSAJES[fallo_jwt.codigo],
            solicitud.id_solicitud,
        )

    # 2. Extraer y validar el contexto de sesión enviado por el cliente.
    # Un contexto inválido se considera un error de la solicitud (400).
    contexto = self.analizador_contexto.analizar(solicitud.cabeceras)
    if hasattr(contexto, "codigo"):
        return ErrorPasarela(
            "invalid_session_context",
            400,
            MENSAJES["invalid_session_context"],
            solicitud.id_solicitud,
        )

    # El detector es obligatorio para tomar la decisión de seguridad.
    # Si no está configurado, se considera un error de configuración interno.
    if not self.cliente_detector:
        raise RuntimeError("downstream policy phases are not configured")

    # 3. Solicitar al Detector la evaluación de riesgo de la sesión.
    # X-Escenario-Stub permite simular escenarios durante los experimentos.
    resultado = self.cliente_detector.evaluar(
        contexto,
        solicitud.id_solicitud,
        solicitud.cabeceras.get("X-Escenario-Stub"),
    )

    # Registrar fallos de disponibilidad del Detector para observabilidad.
    if self.auditoria and resultado.fallo:
        self.auditoria.registrar(
            "disponibilidad",
            dependencia="detector",
            categoria=resultado.fallo.categoria,
            causa=resultado.fallo.causa,
            request_id=solicitud.id_solicitud,
            latencia_ms=resultado.fallo.latencia_ms,
        )

    # Fail closed: si el Detector no puede producir una decisión,
    # la operación protegida no continúa.
    if resultado.fallo:
        return ErrorPasarela(
            "detector_unavailable",
            503,
            "The security decision is temporarily unavailable.",
            solicitud.id_solicitud,
        )

    # 4. Una sesión sospechosa requiere verificación reforzada
    # antes de permitir que la operación llegue al servicio protegido.
    if resultado.veredicto.veredicto == "sospechoso":

        # Si Identidad no está disponible/configurado, la operación
        # permanece bloqueada.
        if not self.cliente_identidad:
            self._auditar_decision(solicitud, resultado, inicio)

            return ErrorPasarela(
                "identity_unavailable",
                503,
                "The verification service is temporarily unavailable.",
                solicitud.id_solicitud,
            )

        # Intentar iniciar el proceso de verificación reforzada.
        identidad = self.cliente_identidad.iniciar_verificacion(
            contexto.id_sesion,
            solicitud.id_solicitud,
            solicitud.cabeceras.get("X-Escenario-Stub"),
        )

        # Si Identidad falla, registrar la causa y mantener
        # bloqueada la operación original.
        if identidad.fallo:
            if self.auditoria:
                self.auditoria.registrar(
                    "disponibilidad",
                    dependencia="identidad",
                    categoria=identidad.fallo.categoria,
                    causa=identidad.fallo.causa,
                    request_id=solicitud.id_solicitud,
                    latencia_ms=identidad.fallo.latencia_ms,
                )

            self._auditar_decision(solicitud, resultado, inicio)

            return ErrorPasarela(
                "identity_unavailable",
                503,
                "The verification service is temporarily unavailable.",
                solicitud.id_solicitud,
            )

        # La verificación fue iniciada correctamente, pero la operación
        # actual sigue bloqueada hasta que el usuario la complete.
        self._auditar_decision(solicitud, resultado, inicio)

        return ErrorPasarela(
            "verification_required",
            403,
            "Additional verification is required.",
            solicitud.id_solicitud,
        )

    # 5. Si el Detector no considera sospechosa la sesión,
    # reenviar la solicitud al servicio protegido (Journey).
    reenviado = self.cliente_journey.reenviar(solicitud)

    # Registrar y traducir fallos del servicio protegido a errores
    # apropiados de Gateway.
    if reenviado.fallo:
        if self.auditoria:
            self.auditoria.registrar(
                "disponibilidad",
                dependencia="journey",
                categoria=reenviado.fallo.categoria,
                causa=reenviado.fallo.causa,
                request_id=solicitud.id_solicitud,
                latencia_ms=reenviado.fallo.latencia_ms,
            )

        self._auditar_decision(solicitud, resultado, inicio)

        # Timeout del downstream -> 504.
        # Otros errores de comunicación/downstream -> 502.
        estado = (
            504
            if reenviado.fallo.categoria == "tiempo_espera"
            else 502
        )

        return ErrorPasarela(
            "journey_unavailable",
            estado,
            "The protected service is temporarily unavailable.",
            solicitud.id_solicitud,
        )

    # Registrar la decisión final y devolver transparentemente
    # la respuesta producida por el servicio protegido.
    self._auditar_decision(solicitud, resultado, inicio)

    return reenviado.respuesta
