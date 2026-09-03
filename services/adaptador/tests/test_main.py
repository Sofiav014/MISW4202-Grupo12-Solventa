"""Tests de integración de /perfil: fallback a caché (3.3) y write-back (3.4).
"""
import json
import unittest
from unittest.mock import patch

from app.circuit_breaker import breaker
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
        from app.cache import CacheMissError

        mock_obtener_perfil.side_effect = OpenFinanceError("conexion", "caído")
        mock_leer_perfil.side_effect = CacheMissError("sin perfil")

        respuesta = self.client.get("/perfil/99999")

        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.get_json()["tipo_error"], "cache_miss")
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


if __name__ == "__main__":
    unittest.main()
