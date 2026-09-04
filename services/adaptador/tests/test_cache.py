"""Tests del fallback a caché (3.3), del write-back (3.4) y del manejo de cache
miss y perfil vencido (3.5).
"""
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.cache import CacheExpiredError, CacheMissError, guardar_perfil, leer_perfil
from app.config import TTL_S


def _hace_segundos(segundos: float) -> str:
    """Timestamp ISO-8601 con sufijo Z, como el que emite Open Finance."""
    marca = datetime.now(timezone.utc) - timedelta(seconds=segundos)
    return marca.isoformat().replace("+00:00", "Z")


PERFIL_CACHEADO = {
    "cliente_id": "12345",
    "score_riesgo": 720,
    "fuente": "CACHE",
    "timestamp_perfil": _hace_segundos(0),
}

PERFIL_DE_OPEN_FINANCE = {
    "cliente_id": "12345",
    "score_riesgo": 720,
    "fuente": "OPEN_FINANCE",
    "timestamp_perfil": _hace_segundos(0),
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

        with self.assertRaises(CacheMissError) as capturado:
            leer_perfil("99999", time.monotonic())

        self.assertEqual(capturado.exception.tipo_error, "CACHE_MISS")
        self.assertEqual(capturado.exception.hit_miss, "MISS")

    @patch("app.cache._cliente_redis")
    def test_hit_registra_los_instantes_crudos_en_el_log(self, mock_redis):
        mock_redis.get.return_value = json.dumps(PERFIL_CACHEADO)
        instante_deteccion = time.monotonic()

        with self.assertLogs("adaptador.cache", level="INFO") as capturado:
            leer_perfil("12345", instante_deteccion)

        linea = capturado.output[0]
        self.assertIn("hit_miss=HIT", linea)
        self.assertIn("cliente_id=12345", linea)
        self.assertIn(f"instante_deteccion={instante_deteccion}", linea)
        self.assertIn("instante_lectura=", linea)

    @patch("app.cache._cliente_redis")
    def test_miss_tambien_registra_el_intento_en_el_log(self, mock_redis):
        mock_redis.get.return_value = None

        with self.assertLogs("adaptador.cache", level="INFO") as capturado:
            with self.assertRaises(CacheMissError):
                leer_perfil("99999", time.monotonic())

        self.assertIn("hit_miss=MISS", capturado.output[0])

    @patch("app.cache._cliente_redis")
    def test_hit_incluye_event_type_cache_op_y_fuente_del_perfil(self, mock_redis):
        mock_redis.get.return_value = json.dumps(PERFIL_CACHEADO)

        with self.assertLogs("adaptador.cache", level="INFO") as capturado:
            leer_perfil("12345", time.monotonic())

        evento = capturado.records[0]
        self.assertEqual(evento.event_type, "cache_op")
        self.assertEqual(evento.operacion, "lectura")
        self.assertEqual(evento.fuente, "CACHE")
        self.assertIsNone(evento.request_id)

    @patch("app.cache._cliente_redis")
    def test_miss_no_tiene_fuente_porque_no_hay_perfil(self, mock_redis):
        mock_redis.get.return_value = None

        with self.assertLogs("adaptador.cache", level="INFO") as capturado:
            with self.assertRaises(CacheMissError):
                leer_perfil("99999", time.monotonic())

        self.assertIsNone(capturado.records[0].fuente)


class TestPerfilVencido(unittest.TestCase):
    """3.5: distinguir "venció" de "nunca existió" evaluando timestamp_perfil."""

    @patch("app.cache._cliente_redis")
    def test_perfil_dentro_del_ttl_se_sirve(self, mock_redis):
        perfil = {**PERFIL_CACHEADO, "timestamp_perfil": _hace_segundos(TTL_S - 10)}
        mock_redis.get.return_value = json.dumps(perfil)

        self.assertEqual(leer_perfil("12345", time.monotonic()), perfil)

    @patch("app.cache._cliente_redis")
    def test_perfil_vencido_lanza_cacheexpirederror(self, mock_redis):
        perfil = {**PERFIL_CACHEADO, "timestamp_perfil": _hace_segundos(TTL_S + 60)}
        mock_redis.get.return_value = json.dumps(perfil)

        with self.assertRaises(CacheExpiredError) as capturado:
            leer_perfil("12345", time.monotonic())

        self.assertEqual(capturado.exception.tipo_error, "CACHE_EXPIRED")
        self.assertEqual(capturado.exception.hit_miss, "EXPIRED")

    @patch("app.cache._cliente_redis")
    def test_vencido_no_se_confunde_con_cache_miss(self, mock_redis):
        mock_redis.get.return_value = json.dumps(
            {**PERFIL_CACHEADO, "timestamp_perfil": _hace_segundos(TTL_S + 60)}
        )

        with self.assertRaises(CacheExpiredError):
            leer_perfil("12345", time.monotonic())

    @patch("app.cache._cliente_redis")
    def test_vencido_registra_hit_miss_expired_en_el_log(self, mock_redis):
        mock_redis.get.return_value = json.dumps(
            {**PERFIL_CACHEADO, "timestamp_perfil": _hace_segundos(TTL_S + 60)}
        )

        with self.assertLogs("adaptador.cache", level="INFO") as capturado:
            with self.assertRaises(CacheExpiredError):
                leer_perfil("12345", time.monotonic())

        self.assertIn("hit_miss=EXPIRED", capturado.output[0])

    @patch("app.cache._cliente_redis")
    def test_perfil_sin_timestamp_se_sirve_como_fresco(self, mock_redis):
        perfil = {k: v for k, v in PERFIL_CACHEADO.items() if k != "timestamp_perfil"}
        mock_redis.get.return_value = json.dumps(perfil)

        self.assertEqual(leer_perfil("12345", time.monotonic()), perfil)

    @patch("app.cache._cliente_redis")
    def test_perfil_con_timestamp_ilegible_se_sirve_como_fresco(self, mock_redis):
        perfil = {**PERFIL_CACHEADO, "timestamp_perfil": "no-es-una-fecha"}
        mock_redis.get.return_value = json.dumps(perfil)

        self.assertEqual(leer_perfil("12345", time.monotonic()), perfil)


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

    @patch("app.cache._cliente_redis")
    def test_escritura_incluye_event_type_cache_op_y_fuente_original(self, mock_redis):
        with self.assertLogs("adaptador.cache", level="INFO") as capturado:
            guardar_perfil("12345", PERFIL_DE_OPEN_FINANCE)

        evento = capturado.records[0]
        self.assertEqual(evento.event_type, "cache_op")
        self.assertEqual(evento.operacion, "escritura")
        # Se registra la fuente ORIGINAL (antes de forzarla a "CACHE" al guardar).
        self.assertEqual(evento.fuente, "OPEN_FINANCE")
        self.assertIsNone(evento.request_id)


if __name__ == "__main__":
    unittest.main()
