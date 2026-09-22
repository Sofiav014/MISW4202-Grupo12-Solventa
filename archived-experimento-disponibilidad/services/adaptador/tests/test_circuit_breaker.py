"""Tests del Circuit Breaker del adaptador."""
import time
import unittest
from unittest.mock import Mock

import pybreaker

from app.circuit_breaker import (
    BreakerEventListener,
    contador_llamadas_evitadas,
    registrar_llamada_evitada,
)

FAIL_MAX = 2
RESET_TIMEOUT_S = 0.05


def _nuevo_breaker():
    return pybreaker.CircuitBreaker(
        fail_max=FAIL_MAX,
        reset_timeout=RESET_TIMEOUT_S,
        listeners=[BreakerEventListener()],
    )


class TestTransicionesCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self.breaker = _nuevo_breaker()

    def _abrir_circuito(self, protegida):
        for _ in range(FAIL_MAX - 1):
            with self.assertRaises(RuntimeError):
                protegida()
        with self.assertRaises(pybreaker.CircuitBreakerError):
            protegida()

    def test_circuito_inicia_cerrado(self):
        self.assertEqual(self.breaker.current_state, "closed")

    def test_abre_tras_fail_max_fallas_consecutivas(self):
        llamada_que_falla = Mock(side_effect=RuntimeError("proveedor caido"))
        protegida = self.breaker(llamada_que_falla)

        self._abrir_circuito(protegida)

        self.assertEqual(self.breaker.current_state, "open")

    def test_circuito_abierto_no_invoca_la_funcion_protegida(self):
        llamada_que_falla = Mock(side_effect=RuntimeError("proveedor caido"))
        protegida = self.breaker(llamada_que_falla)
        self._abrir_circuito(protegida)
        llamada_que_falla.reset_mock()

        with self.assertRaises(pybreaker.CircuitBreakerError):
            protegida()

        llamada_que_falla.assert_not_called()

    def test_pasa_a_half_open_tras_reset_timeout_y_cierra_si_la_prueba_funciona(self):
        efectos = [RuntimeError()] * (FAIL_MAX - 1) + [RuntimeError(), "perfil-ok"]
        llamada = Mock(side_effect=efectos)
        protegida = self.breaker(llamada)
        self._abrir_circuito(protegida)
        self.assertEqual(self.breaker.current_state, "open")

        time.sleep(RESET_TIMEOUT_S * 1.5)

        self.assertEqual(protegida(), "perfil-ok")
        self.assertEqual(self.breaker.current_state, "closed")

    def test_vuelve_a_abrir_si_la_prueba_en_half_open_falla(self):
        siempre_falla = Mock(side_effect=RuntimeError("proveedor caido"))
        protegida = self.breaker(siempre_falla)
        self._abrir_circuito(protegida)
        self.assertEqual(self.breaker.current_state, "open")

        time.sleep(RESET_TIMEOUT_S * 1.5)

        # La prueba en HALF_OPEN falla: pybreaker reabre y relanza
        # CircuitBreakerError, no la excepción original.
        with self.assertRaises(pybreaker.CircuitBreakerError):
            protegida()
        self.assertEqual(self.breaker.current_state, "open")

    def test_listener_registra_las_transiciones_de_estado(self):
        eventos = []
        breaker = pybreaker.CircuitBreaker(
            fail_max=FAIL_MAX,
            reset_timeout=RESET_TIMEOUT_S,
        )
        breaker.add_listener(_ListenerDePrueba(eventos))
        llamada = Mock(side_effect=[RuntimeError()] * (FAIL_MAX - 1) + [RuntimeError(), "ok"])
        protegida = breaker(llamada)

        for _ in range(FAIL_MAX - 1):
            with self.assertRaises(RuntimeError):
                protegida()
        with self.assertRaises(pybreaker.CircuitBreakerError):
            protegida()
        self.assertIn(("closed", "open"), eventos)

        time.sleep(RESET_TIMEOUT_S * 1.5)
        self.assertEqual(protegida(), "ok")
        self.assertIn(("open", "half-open"), eventos)
        self.assertIn(("half-open", "closed"), eventos)


class _ListenerDePrueba(pybreaker.CircuitBreakerListener):
    def __init__(self, eventos):
        self.eventos = eventos

    def state_change(self, cb, old_state, new_state):
        self.eventos.append((old_state.name, new_state.name))


class TestBreakerEventListenerLoggea(unittest.TestCase):
    """Las transiciones se registran con event_type y request_id, no solo como
    texto plano, para que se puedan filtrar en Pandas."""

    def test_transicion_incluye_event_type_cb_transition(self):
        breaker = _nuevo_breaker()
        protegida = breaker(Mock(side_effect=RuntimeError("x")))

        with self.assertLogs("adaptador.circuit_breaker", level="INFO") as capturado:
            for _ in range(FAIL_MAX - 1):
                with self.assertRaises(RuntimeError):
                    protegida()
            with self.assertRaises(pybreaker.CircuitBreakerError):
                protegida()

        evento = capturado.records[0]
        self.assertEqual(evento.event_type, "cb_transition")
        self.assertEqual(evento.estado_anterior, "closed")
        self.assertEqual(evento.estado_nuevo, "open")

    def test_request_id_es_none_fuera_de_un_contexto_de_peticion(self):
        breaker = _nuevo_breaker()
        protegida = breaker(Mock(side_effect=RuntimeError("x")))

        with self.assertLogs("adaptador.circuit_breaker", level="INFO") as capturado:
            for _ in range(FAIL_MAX - 1):
                with self.assertRaises(RuntimeError):
                    protegida()
            with self.assertRaises(pybreaker.CircuitBreakerError):
                protegida()

        self.assertIsNone(capturado.records[0].request_id)


class TestContadorLlamadasEvitadas(unittest.TestCase):
    def test_registrar_llamada_evitada_incrementa_el_contador_en_uno(self):
        antes = contador_llamadas_evitadas()

        registrar_llamada_evitada()

        self.assertEqual(contador_llamadas_evitadas(), antes + 1)

    def test_registra_el_evento_con_event_type_cb_call_skipped(self):
        with self.assertLogs("adaptador.circuit_breaker", level="INFO") as capturado:
            registrar_llamada_evitada()

        evento = capturado.records[0]
        self.assertEqual(evento.event_type, "cb_call_skipped")
        self.assertIsNone(evento.request_id)
        self.assertEqual(evento.total_llamadas_evitadas, contador_llamadas_evitadas())


if __name__ == "__main__":
    unittest.main()
