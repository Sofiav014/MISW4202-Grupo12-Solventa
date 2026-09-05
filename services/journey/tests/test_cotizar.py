import unittest
from unittest.mock import Mock, patch

from app.main import app


PERFIL_OPEN_FINANCE = {
    "cliente_id": "12345",
    "score_riesgo": 720,
    "fuente": "OPEN_FINANCE",
    "timestamp_perfil": "2026-08-31T10:00:00Z",
}


class CotizarTest(unittest.TestCase):
    @patch("app.main.requests.get")
    def test_respeta_la_fuente_open_finance(self, requests_get):
        respuesta_adaptador = Mock()
        respuesta_adaptador.json.return_value = PERFIL_OPEN_FINANCE
        requests_get.return_value = respuesta_adaptador

        with app.test_client() as client:
            response = client.post("/cotizar")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["fuente_perfil"], "OPEN_FINANCE")

    @patch("app.main.requests.get")
    def test_respeta_la_fuente_cache_cuando_esta_presente(self, requests_get):
        respuesta_adaptador = Mock()
        respuesta_adaptador.json.return_value = {
            **PERFIL_OPEN_FINANCE,
            "fuente": "CACHE",
        }
        requests_get.return_value = respuesta_adaptador

        with app.test_client() as client:
            response = client.post("/cotizar")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["fuente_perfil"], "CACHE")


if __name__ == "__main__":
    unittest.main()
