import unittest
from unittest.mock import Mock, patch

from app.main import app


PERFIL_OPEN_FINANCE = {
    "clienteId": "12345",
    "puntajeEstabilidadIngresos": 85,
    "relacionDeudaIngresos": 0.35,
    "puntajeComportamientoPago": 90,
    "incumplimientos12Meses": 0,
    "periodoInformacion": "2025-01-01/2025-12-31",
    "fechaVigenciaDatos": "2026-12-31",
}


class CotizarTest(unittest.TestCase):
    @patch("app.main.requests.get")
    def test_informa_open_finance_cuando_el_dto_no_incluye_fuente(self, requests_get):
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
