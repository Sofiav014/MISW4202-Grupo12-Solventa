"""Tests de logging_json.py (4.2).

Cubren el JsonFormatter, el helper _ms, configurar_logging, y sobre todo
instrumentar_peticiones: el before/after_request que arma un evento
"request" por petición a /perfil con las columnas de la Fase 0.4,
incluyendo proveedor_invocado (la señal de "llamada evitada con el
circuito abierto").
"""
import json
import logging
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, g, request

from app.logging_json import (
    TIPO_ERROR_PROVEEDOR,
    JsonFormatter,
    _ms,
    configurar_logging,
    instrumentar_peticiones,
)


def _crear_app_de_prueba(estado_circuito):
    """App Flask mínima con una ruta 'perfil' que simula los distintos
    caminos reales de main.py, y una 'health' que no debe instrumentarse."""
    app = Flask(__name__)

    @app.route("/perfil/<cliente_id>", endpoint="perfil")
    def perfil(cliente_id):
        modo = request.args.get("modo", "exito")
        if modo == "exito":
            g.hit_miss, g.fuente_respuesta, g.resultado = "N/A", "PROVIDER", "exitoso"
        elif modo == "fallback_hit":
            g.timestamp_deteccion = 1.0
            g.timestamp_respuesta_cache = 1.2
            g.hit_miss, g.fuente_respuesta = "HIT", "CACHE"
            g.resultado, g.tipo_error = "degradado", "CIRCUIT_OPEN"
        elif modo == "sin_dato":
            g.timestamp_deteccion = 1.0
            g.timestamp_respuesta_cache = 1.1
            g.hit_miss, g.fuente_respuesta = "MISS", "NONE"
            g.resultado, g.tipo_error = "fallido", "CACHE_MISS"
        return {"ok": True}

    @app.route("/health", endpoint="health")
    def health():
        return {"status": "ok"}

    instrumentar_peticiones(app, estado_circuito)
    return app


class TestInstrumentarPeticiones(unittest.TestCase):
    def _cliente(self, estado_circuito):
        return _crear_app_de_prueba(estado_circuito).test_client()

    def test_emite_un_evento_request_por_peticion_a_perfil(self):
        client = self._cliente(lambda: "CLOSED")

        with self.assertLogs("adaptador.request", level="INFO") as capturado:
            client.get("/perfil/12345?modo=exito")

        self.assertEqual(len(capturado.records), 1)
        evento = capturado.records[0]
        self.assertEqual(evento.hit_miss, "N/A")
        self.assertEqual(evento.fuente_respuesta, "PROVIDER")
        self.assertEqual(evento.resultado, "exitoso")
        self.assertEqual(evento.estado_circuito_inicio, "CLOSED")
        self.assertEqual(evento.estado_circuito_fin, "CLOSED")
        self.assertTrue(evento.proveedor_invocado)

    def test_health_no_genera_evento_request(self):
        client = self._cliente(lambda: "CLOSED")

        with self.assertRaises(AssertionError):
            with self.assertLogs("adaptador.request", level="INFO"):
                client.get("/health")

    def test_circuito_abierto_al_inicio_marca_proveedor_no_invocado(self):
        client = self._cliente(lambda: "OPEN")

        with self.assertLogs("adaptador.request", level="INFO") as capturado:
            client.get("/perfil/12345?modo=fallback_hit")

        self.assertFalse(capturado.records[0].proveedor_invocado)

    def test_circuito_cerrado_al_inicio_marca_proveedor_invocado_aunque_termine_abierto(self):
        estados = iter(["CLOSED", "OPEN"])
        client = self._cliente(lambda: next(estados))

        with self.assertLogs("adaptador.request", level="INFO") as capturado:
            client.get("/perfil/12345?modo=fallback_hit")

        evento = capturado.records[0]
        self.assertTrue(evento.proveedor_invocado)
        self.assertEqual(evento.estado_circuito_inicio, "CLOSED")
        self.assertEqual(evento.estado_circuito_fin, "OPEN")

    def test_usa_x_request_id_del_header_si_viene(self):
        client = self._cliente(lambda: "CLOSED")

        with self.assertLogs("adaptador.request", level="INFO") as capturado:
            client.get("/perfil/12345?modo=exito", headers={"X-Request-Id": "abc-123"})

        self.assertEqual(capturado.records[0].request_id, "abc-123")

    def test_genera_request_id_si_no_viene_el_header(self):
        client = self._cliente(lambda: "CLOSED")

        with self.assertLogs("adaptador.request", level="INFO") as capturado:
            client.get("/perfil/12345?modo=exito")

        self.assertTrue(capturado.records[0].request_id.startswith("local-"))

    def test_sin_dato_calcula_las_tres_latencias(self):
        client = self._cliente(lambda: "OPEN")

        with self.assertLogs("adaptador.request", level="INFO") as capturado:
            client.get("/perfil/99999?modo=sin_dato")

        evento = capturado.records[0]
        self.assertIsNotNone(evento.latencia_proveedor_ms)
        self.assertIsNotNone(evento.tiempo_conmutacion_ms)
        self.assertIsNotNone(evento.latencia_total_ms)

    def test_exito_no_tiene_latencia_de_proveedor_ni_conmutacion(self):
        client = self._cliente(lambda: "CLOSED")

        with self.assertLogs("adaptador.request", level="INFO") as capturado:
            client.get("/perfil/12345?modo=exito")

        evento = capturado.records[0]
        self.assertIsNone(evento.latencia_proveedor_ms)
        self.assertIsNone(evento.tiempo_conmutacion_ms)
        self.assertIsNotNone(evento.latencia_total_ms)


class TestMs(unittest.TestCase):
    def test_retorna_none_si_falta_cualquiera_de_los_dos(self):
        self.assertIsNone(_ms(None, 5.0))
        self.assertIsNone(_ms(5.0, None))
        self.assertIsNone(_ms(None, None))

    def test_calcula_milisegundos_correctamente(self):
        self.assertAlmostEqual(_ms(1.0, 1.25), 250.0)


class TestJsonFormatter(unittest.TestCase):
    def setUp(self):
        self.formatter = JsonFormatter()

    def _formatear(self, logger_name, msg, extra=None):
        logger = logging.getLogger(logger_name)
        registro = logger.makeRecord(
            logger_name, logging.INFO, __file__, 0, msg, (), None, extra=extra
        )
        return json.loads(self.formatter.format(registro))

    def test_incluye_metadatos_base(self):
        evento = self._formatear("adaptador.cache", "cache_write cliente_id=12345")

        self.assertEqual(evento["logger"], "adaptador.cache")
        self.assertEqual(evento["level"], "INFO")
        self.assertIn("ts_wall", evento)

    def test_incluye_los_campos_de_extra(self):
        evento = self._formatear(
            "adaptador.cache",
            "cache_fallback hit_miss=HIT",
            extra={"hit_miss": "HIT", "cliente_id": "12345"},
        )

        self.assertEqual(evento["hit_miss"], "HIT")
        self.assertEqual(evento["cliente_id"], "12345")

    def test_sin_event_type_explicito_no_lo_agrega(self):
        """El formatter no infiere event_type: cada logger.info(...) debe
        pasarlo en extra, o el evento queda sin esa columna."""
        evento = self._formatear("adaptador.cache", "cache_write cliente_id=12345")

        self.assertNotIn("event_type", evento)
        self.assertNotIn("mensaje", evento)

    def test_respeta_event_type_explicito_sin_agregar_mensaje(self):
        evento = self._formatear(
            "adaptador.request",
            "request",
            extra={"event_type": "request", "resultado": "exitoso"},
        )

        self.assertEqual(evento["event_type"], "request")
        self.assertNotIn("mensaje", evento)

    def test_no_incluye_atributos_internos_del_logrecord(self):
        evento = self._formatear("adaptador.cache", "algo", extra={"clave": "valor"})

        for interno in ("msg", "args", "levelno", "pathname", "exc_info"):
            self.assertNotIn(interno, evento)


class TestConfigurarLogging(unittest.TestCase):
    def tearDown(self):
        logging.getLogger().handlers.clear()

    @patch("app.logging_json.LOG_PATH", None)
    def test_sin_log_path_solo_instala_stream_handler(self):
        configurar_logging()

        raiz = logging.getLogger()
        self.assertEqual(len(raiz.handlers), 1)
        self.assertIsInstance(raiz.handlers[0], logging.StreamHandler)
        self.assertIsInstance(raiz.handlers[0].formatter, JsonFormatter)

    def test_con_log_path_tambien_escribe_a_archivo(self):
        tmp = tempfile.mkdtemp()
        try:
            ruta = os.path.join(tmp, "sub", "adaptador.jsonl")
            with patch("app.logging_json.LOG_PATH", ruta):
                configurar_logging()

            logging.getLogger("adaptador.cache").info(
                "cache_write cliente_id=12345",
                extra={"event_type": "cache_op", "cliente_id": "12345"},
            )

            raiz = logging.getLogger()
            for handler in raiz.handlers:
                handler.flush()
                if isinstance(handler, logging.FileHandler):
                    handler.close()
            raiz.handlers.clear()

            self.assertTrue(os.path.exists(ruta))
            with open(ruta, encoding="utf-8") as archivo:
                lineas = archivo.readlines()
            self.assertEqual(len(lineas), 1)
            self.assertEqual(json.loads(lineas[0])["cliente_id"], "12345")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestTipoError04(unittest.TestCase):
    def test_mapea_los_tipos_conocidos_de_openfinanceerror(self):
        self.assertEqual(TIPO_ERROR_PROVEEDOR["timeout"], "PROVIDER_TIMEOUT")
        self.assertEqual(TIPO_ERROR_PROVEEDOR["conexion"], "PROVIDER_UNAVAILABLE")
        self.assertEqual(TIPO_ERROR_PROVEEDOR["respuesta_invalida"], "PROVIDER_INVALID_RESPONSE")


if __name__ == "__main__":
    unittest.main()
