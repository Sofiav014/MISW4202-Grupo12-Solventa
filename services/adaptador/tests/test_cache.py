"""Tests del fallback a caché (3.3) y del write-back (3.4).
"""
import json
import time
import unittest
from unittest.mock import patch

from app.cache import CacheMissError, guardar_perfil, leer_perfil
from app.config import TTL_S

PERFIL_CACHEADO = {
    "cliente_id": "12345",
    "score_riesgo": 720,
    "fuente": "CACHE",
    "timestamp_perfil": "2026-08-31T10:00:00Z",
}

PERFIL_DE_OPEN_FINANCE = {
    "cliente_id": "12345",
    "score_riesgo": 720,
    "fuente": "OPEN_FINANCE",
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


class TestGuardarPerfilEnCache(unittest.TestCase):
    @patch("app.cache._cliente_redis")
    def test_escribe_con_la_clave_y_ttl_correctos(self, mock_redis):
        guardar_perfil("12345", PERFIL_DE_OPEN_FINANCE)

        mock_redis.set.assert_called_once()
        (clave, valor), kwargs = mock_redis.set.call_args
        self.assertEqual(clave, "perfil:12345")
        self.assertEqual(kwargs["ex"], TTL_S)
        self.assertEqual(json.loads(valor), {**PERFIL_DE_OPEN_FINANCE, "fuente": "CACHE"})

    @patch("app.cache._cliente_redis")
    def test_no_modifica_el_diccionario_original(self, mock_redis):
        perfil_original = dict(PERFIL_DE_OPEN_FINANCE)

        guardar_perfil("12345", perfil_original)

        self.assertEqual(perfil_original["fuente"], "OPEN_FINANCE")

    @patch("app.cache._cliente_redis")
    def test_registra_la_escritura_en_el_log(self, mock_redis):
        with self.assertLogs("adaptador.cache", level="INFO") as capturado:
            guardar_perfil("12345", PERFIL_DE_OPEN_FINANCE)

        linea = capturado.output[0]
        self.assertIn("cliente_id=12345", linea)
        self.assertIn(f"ttl_s={TTL_S}", linea)


if __name__ == "__main__":
    unittest.main()
