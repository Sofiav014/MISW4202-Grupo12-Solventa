"""Tests del cliente de Open Finance.

Cubren el mapeo de fallas del proveedor a OpenFinanceError y la configuración
del timeout exigida por el ASR, simulando las respuestas del proveedor con
dobles de prueba.
"""
import unittest
from unittest.mock import Mock, patch

import requests

from app.clientes.open_finance import OpenFinanceClient, OpenFinanceError

PERFIL_VALIDO = {
    "cliente_id": "12345",
    "score_riesgo": 720,
    "fuente": "OPEN_FINANCE",
    "timestamp_perfil": "2026-08-31T10:00:00Z",
}


def _respuesta_simulada(status_code=200, json_data=None):
    respuesta = Mock()
    respuesta.status_code = status_code
    respuesta.json.return_value = json_data
    return respuesta


@patch("app.clientes.open_finance.requests.get")
class TestOpenFinanceClient(unittest.TestCase):
    def setUp(self):
        self.cliente = OpenFinanceClient()

    def test_timeout_se_traduce_a_openfinance_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout()

        with self.assertRaises(OpenFinanceError) as cm:
            self.cliente.obtener_perfil("12345")

        self.assertEqual(cm.exception.tipo, "timeout")

    def test_error_de_conexion_se_traduce_a_openfinance_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError()

        with self.assertRaises(OpenFinanceError) as cm:
            self.cliente.obtener_perfil("12345")

        self.assertEqual(cm.exception.tipo, "conexion")

    def test_status_code_distinto_de_200_es_respuesta_invalida(self, mock_get):
        mock_get.return_value = _respuesta_simulada(status_code=500)

        with self.assertRaises(OpenFinanceError) as cm:
            self.cliente.obtener_perfil("12345")

        self.assertEqual(cm.exception.tipo, "respuesta_invalida")

    def test_perfil_sin_campos_del_contrato_es_respuesta_invalida(self, mock_get):
        perfil_sin_fuente_ni_timestamp = {"cliente_id": "12345", "score_riesgo": 720}
        mock_get.return_value = _respuesta_simulada(
            json_data=perfil_sin_fuente_ni_timestamp
        )

        with self.assertRaises(OpenFinanceError) as cm:
            self.cliente.obtener_perfil("12345")

        self.assertEqual(cm.exception.tipo, "respuesta_invalida")

    def test_perfil_valido_se_retorna_tal_cual(self, mock_get):
        mock_get.return_value = _respuesta_simulada(json_data=PERFIL_VALIDO)

        resultado = self.cliente.obtener_perfil("12345")

        self.assertEqual(resultado, PERFIL_VALIDO)

    def test_timeout_configurado_es_tupla_connect_read_no_float_unico(self, mock_get):
        mock_get.return_value = _respuesta_simulada(json_data=PERFIL_VALIDO)

        self.cliente.obtener_perfil("12345")

        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["timeout"], (0.2, 0.7))


if __name__ == "__main__":
    unittest.main()
