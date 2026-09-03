"""Circuit Breaker que protege las llamadas a Open Finance."""
import logging

import pybreaker

from app.config import FAIL_MAX, RESET_TIMEOUT_S

logger = logging.getLogger("adaptador.circuit_breaker")


class BreakerEventListener(pybreaker.CircuitBreakerListener):
    def state_change(self, cb, old_state, new_state):
        logger.info(
            "circuit_breaker_transition estado_anterior=%s estado_nuevo=%s",
            old_state.name,
            new_state.name,
            extra={
                "estado_anterior": old_state.name,
                "estado_nuevo": new_state.name,
            },
        )


breaker = pybreaker.CircuitBreaker(
    fail_max=FAIL_MAX,
    reset_timeout=RESET_TIMEOUT_S,
    listeners=[BreakerEventListener()],
)
