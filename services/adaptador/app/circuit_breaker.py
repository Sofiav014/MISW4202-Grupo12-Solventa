"""Circuit Breaker que protege las llamadas a Open Finance."""
import logging

import pybreaker
from flask import g, has_request_context

from app.config import FAIL_MAX, RESET_TIMEOUT_S

logger = logging.getLogger("adaptador.circuit_breaker")


def _request_id_actual():
    """El request_id de la petición en curso (lo deja logging_json.py en g),
    o None fuera de un contexto de petición (p. ej. en tests)."""
    return g.get("request_id") if has_request_context() else None


class BreakerEventListener(pybreaker.CircuitBreakerListener):
    def state_change(self, cb, old_state, new_state):
        logger.info(
            "circuit_breaker_transition estado_anterior=%s estado_nuevo=%s",
            old_state.name,
            new_state.name,
            extra={
                "event_type": "cb_transition",
                "request_id": _request_id_actual(),
                "estado_anterior": old_state.name,
                "estado_nuevo": new_state.name,
            },
        )


breaker = pybreaker.CircuitBreaker(
    fail_max=FAIL_MAX,
    reset_timeout=RESET_TIMEOUT_S,
    listeners=[BreakerEventListener()],
)

_llamadas_evitadas = 0


def contador_llamadas_evitadas() -> int:
    """Cuántas peticiones llegaron con el circuito ya abierto y no llegaron
    a intentar la llamada al proveedor."""
    return _llamadas_evitadas


def registrar_llamada_evitada() -> None:
    """Cuenta y registra una petición evitada por circuito abierto.

    Se llama desde main.py justo antes de invocar el breaker, cuando ya se
    sabe que breaker.current_state == "open" - ahí es donde se detecta,
    no aquí.
    """
    global _llamadas_evitadas
    _llamadas_evitadas += 1
    logger.info(
        "circuit_breaker_call_skipped total=%s",
        _llamadas_evitadas,
        extra={
            "event_type": "cb_call_skipped",
            "request_id": _request_id_actual(),
            "total_llamadas_evitadas": _llamadas_evitadas,
        },
    )
