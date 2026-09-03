"""Tests del fallback a caché (3.3).

Cubren cache hit, cache miss, y que se registran (via logging) los
instantes crudos de detección y de lectura de caché. El cálculo del
tiempo de conmutación como métrica es de 4.2, no se prueba aquí.
"""
import json
import time
import unittest
from unittest.mock import patch

from app.cache import CacheMissError, leer_perfil

PERFIL_CACHEADO = {
    "cliente_id": "12345",
    "score_riesgo": 720,
    "fuente": "CACHE",
    "timestamp_perfil": "2026-08-31T10:00:00Z",
}


class TestLeerPerfilDeCache(unittest.TestCase):
    @patch("app.cache._cliente_redis")
    def test_cache_hit_devuelve_el_perfil_guardado(self, mock_redis):
        mock_redis.get.return_value = json.dumps(PERFIL_CACHEADO)

        resultado = leer_perfil("12345", time.monotonic())

        self.assertEqual(resultado, PERFIL_CACHEADO)
        mock_redis.get.assert_called_once_with("perfil:12345")

    @patch("app.cache._cliente_redis")
    def test_cache_miss_lanza_cachemisserror(self, mock_redis):
        mock_redis.get.return_value = None

        with self.assertRaises(CacheMissError):
            leer_perfil("99999", time.monotonic())

    @patch("app.cache._cliente_redis")
    def test_hit_registra_los_instantes_crudos_en_el_log(self, mock_redis):
        mock_redis.get.return_value = json.dumps(PERFIL_CACHEADO)
        instante_deteccion = time.monotonic()

        with self.assertLogs("adaptador.cache", level="INFO") as capturado:
            leer_perfil("12345", instante_deteccion)

        linea = capturado.output[0]
        self.assertIn("resultado=hit", linea)
        self.assertIn("cliente_id=12345", linea)
        self.assertIn(f"instante_deteccion={instante_deteccion}", linea)
        self.assertIn("instante_lectura=", linea)

    @patch("app.cache._cliente_redis")
    def test_miss_tambien_registra_el_intento_en_el_log(self, mock_redis):
        mock_redis.get.return_value = None

        with self.assertLogs("adaptador.cache", level="INFO") as capturado:
            with self.assertRaises(CacheMissError):
                leer_perfil("99999", time.monotonic())

        self.assertIn("resultado=miss", capturado.output[0])


if __name__ == "__main__":
    unittest.main()
