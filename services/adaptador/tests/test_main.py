"""Tests de integración de /perfil: fallback a caché, write-back y manejo de
cache miss y perfil vencido.
"""
import json
import unittest
from unittest.mock import ANY, patch

from app.cache import CacheExpiredError, CacheMissError
from app.circuit_breaker import breaker, contador_llamadas_evitadas
from app.clientes.open_finance import OpenFinanceError
from app.main import app

PERFIL_CACHEADO = {
    "cliente_id": "12345",
    "score_riesgo": 720,
    "fuente": "CACHE",
    "timestamp_perfil": "2026-08-31T10:00:00Z",
}


class TestFallbackACacheEnElEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        breaker.close()  # ningun test hereda el estado del anterior

    def tearDown(self):
        breaker.close()

    @patch("app.main.guardar_perfil")
    @patch("app.main.leer_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_circuito_abierto_sirve_el_perfil_desde_cache(
        self, mock_obtener_perfil, mock_leer_perfil, mock_guardar_perfil
    ):
        mock_obtener_perfil.side_effect = OpenFinanceError("timeout", "no respondió")
        mock_leer_perfil.return_value = PERFIL_CACHEADO

        respuesta = self.client.get("/perfil/12345")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json(), PERFIL_CACHEADO)
        mock_guardar_perfil.assert_not_called()

    @patch("app.main.guardar_perfil")
    @patch("app.main.leer_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_circuito_abierto_sin_perfil_en_cache_devuelve_503(
        self, mock_obtener_perfil, mock_leer_perfil, mock_guardar_perfil
    ):
        mock_obtener_perfil.side_effect = OpenFinanceError("conexion", "caído")
        mock_leer_perfil.side_effect = CacheMissError("sin perfil")

        respuesta = self.client.get("/perfil/99999")

        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.get_json()["tipo_error"], "CACHE_MISS")
        mock_guardar_perfil.assert_not_called()

    @patch("app.main.guardar_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_proveedor_ok_escribe_en_cache_y_responde_el_perfil_original(
        self, mock_obtener_perfil, mock_guardar_perfil
    ):
        perfil_real = {**PERFIL_CACHEADO, "fuente": "OPEN_FINANCE"}
        mock_obtener_perfil.return_value = perfil_real

        respuesta = self.client.get("/perfil/12345")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json(), perfil_real)
        mock_guardar_perfil.assert_called_once_with("12345", perfil_real)


class TestCondicionLimite(unittest.TestCase):
    """Cache miss y perfil vencido como condición límite, no como error genérico."""

    def setUp(self):
        self.client = app.test_client()
        breaker.close()

    def tearDown(self):
        breaker.close()

    @patch("app.main.guardar_perfil")
    @patch("app.main.leer_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_cache_miss_puro_se_etiqueta_como_condicion_limite(
        self, mock_obtener_perfil, mock_leer_perfil, mock_guardar_perfil
    ):
        mock_obtener_perfil.side_effect = OpenFinanceError("timeout", "no respondió")
        mock_leer_perfil.side_effect = CacheMissError("sin perfil")

        respuesta = self.client.get("/perfil/99999")
        cuerpo = respuesta.get_json()

        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(cuerpo["resultado"], "fallido")
        self.assertEqual(cuerpo["fuente_respuesta"], "NONE")
        self.assertEqual(cuerpo["hit_miss"], "MISS")
        self.assertEqual(cuerpo["tipo_error"], "CACHE_MISS")
        self.assertTrue(cuerpo["condicion_limite"])
        mock_guardar_perfil.assert_not_called()

    @patch("app.main.guardar_perfil")
    @patch("app.main.leer_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_perfil_vencido_se_distingue_del_cache_miss(
        self, mock_obtener_perfil, mock_leer_perfil, mock_guardar_perfil
    ):
        mock_obtener_perfil.side_effect = OpenFinanceError("timeout", "no respondió")
        mock_leer_perfil.side_effect = CacheExpiredError("perfil vencido")

        respuesta = self.client.get("/perfil/12345")
        cuerpo = respuesta.get_json()

        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(cuerpo["resultado"], "fallido")
        self.assertEqual(cuerpo["fuente_respuesta"], "NONE")
        self.assertEqual(cuerpo["hit_miss"], "EXPIRED")
        self.assertEqual(cuerpo["tipo_error"], "CACHE_EXPIRED")
        self.assertTrue(cuerpo["condicion_limite"])
        mock_guardar_perfil.assert_not_called()

    @patch("app.main.leer_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_sin_dato_que_servir_no_devuelve_perfil(
        self, mock_obtener_perfil, mock_leer_perfil
    ):
        mock_obtener_perfil.side_effect = OpenFinanceError("conexion", "caído")

        for excepcion in (
            CacheMissError("sin perfil"),
            CacheExpiredError("perfil vencido"),
        ):
            with self.subTest(excepcion=type(excepcion).__name__):
                mock_leer_perfil.side_effect = excepcion
                breaker.close()

                respuesta = self.client.get("/perfil/12345")

                self.assertEqual(respuesta.status_code, 503)
                self.assertNotIn("score_riesgo", respuesta.get_json())


class TestContadorLlamadasEvitadasEnElEndpoint(unittest.TestCase):
    """Cuenta las peticiones que llegan con el circuito ya abierto."""

    def setUp(self):
        self.client = app.test_client()
        breaker.close()

    def tearDown(self):
        breaker.close()

    @patch("app.main.guardar_perfil")
    @patch("app.main.leer_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_segunda_peticion_con_circuito_ya_abierto_incrementa_el_contador(
        self, mock_obtener_perfil, mock_leer_perfil, mock_guardar_perfil
    ):
        mock_obtener_perfil.side_effect = OpenFinanceError("timeout", "no respondió")
        mock_leer_perfil.return_value = PERFIL_CACHEADO

        self.client.get("/perfil/12345")  # esta abre el circuito, no es "evitada"
        self.assertEqual(breaker.current_state, "open")
        antes = contador_llamadas_evitadas()

        self.client.get("/perfil/12345")  # esta ya llega con el circuito abierto

        self.assertEqual(contador_llamadas_evitadas(), antes + 1)

    @patch("app.main.guardar_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_circuito_cerrado_no_incrementa_el_contador(
        self, mock_obtener_perfil, mock_guardar_perfil
    ):
        mock_obtener_perfil.return_value = PERFIL_CACHEADO
        antes = contador_llamadas_evitadas()

        self.client.get("/perfil/12345")

        self.assertEqual(contador_llamadas_evitadas(), antes)


class TestBypassDirectoConCircuitoAbierto(unittest.TestCase):
    """Con el circuito ya abierto, no se debe volver a invocar a Open Finance:
    el fallback a Redis debe ser directo, con o sin dato servible.
    """

    def setUp(self):
        self.client = app.test_client()
        breaker.close()

    def tearDown(self):
        breaker.close()

    @patch("app.main.guardar_perfil")
    @patch("app.main.leer_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_con_perfil_en_cache_no_invoca_open_finance(
        self, mock_obtener_perfil, mock_leer_perfil, mock_guardar_perfil
    ):
        mock_obtener_perfil.side_effect = OpenFinanceError("timeout", "no respondió")
        mock_leer_perfil.return_value = PERFIL_CACHEADO

        self.client.get("/perfil/12345")  # esta falla y abre el circuito
        self.assertEqual(breaker.current_state, "open")
        mock_obtener_perfil.reset_mock()

        respuesta = self.client.get("/perfil/12345")  # ya llega con el circuito abierto

        mock_obtener_perfil.assert_not_called()
        mock_leer_perfil.assert_called_with("12345", ANY)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json(), PERFIL_CACHEADO)
        mock_guardar_perfil.assert_not_called()

    @patch("app.main.guardar_perfil")
    @patch("app.main.leer_perfil")
    @patch("app.main.open_finance.obtener_perfil")
    def test_sin_perfil_en_cache_tampoco_invoca_open_finance(
        self, mock_obtener_perfil, mock_leer_perfil, mock_guardar_perfil
    ):
        mock_obtener_perfil.side_effect = OpenFinanceError("timeout", "no respondió")
        mock_leer_perfil.side_effect = CacheMissError("sin perfil")

        self.client.get("/perfil/99999")  # esta falla y abre el circuito
        self.assertEqual(breaker.current_state, "open")
        mock_obtener_perfil.reset_mock()

        respuesta = self.client.get("/perfil/99999")  # ya llega con el circuito abierto

        mock_obtener_perfil.assert_not_called()
        mock_leer_perfil.assert_called_with("99999", ANY)
        self.assertEqual(respuesta.status_code, 503)
        mock_guardar_perfil.assert_not_called()


if __name__ == "__main__":
    unittest.main()
