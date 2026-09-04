"""Pruebas de construccion, serializacion y persistencia del manifest 4.3."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from experimentos.manifest import (
    ConfiguracionCarga,
    ConfiguracionMockOpenFinance,
    ErrorPersistenciaManifest,
    ErrorSerializacionManifest,
    ManifestCorrida,
    ManifestExistenteError,
    construir_manifest,
    guardar_manifest,
    serializar_manifest,
)


TIMESTAMP_FIJO = datetime(2026, 9, 3, 18, 30, tzinfo=timezone.utc)


def _fuente_configuracion(**cambios: object) -> SimpleNamespace:
    """Crea una fuente de configuracion aislada con datos exclusivos de test."""

    valores = {
        "EJECUCION_ID": "B-02",
        "ESCENARIO": "B",
        "FAIL_MAX": 3,
        "RESET_TIMEOUT_S": 15,
        "TTL_S": 240,
        "TIMEOUT_MS": 700,
        "LOG_DIR": None,
    }
    valores.update(cambios)
    return SimpleNamespace(**valores)


def _manifest_prueba(**cambios_fuente: object) -> ManifestCorrida:
    """Construye un manifest determinista para las aserciones de la suite."""

    return construir_manifest(
        ConfiguracionMockOpenFinance(modo="lento"),
        ConfiguracionCarga(
            usuarios=25,
            duration_seconds=90,
            spawn_rate=5,
        ),
        fuente_configuracion=_fuente_configuracion(**cambios_fuente),
        timestamp=TIMESTAMP_FIJO,
    )


class TestConstruccionManifest(unittest.TestCase):
    """Valida el ensamblaje del DTO y sus entradas."""

    def test_construye_con_configuracion_real_y_snapshots_externos(self) -> None:
        manifest = _manifest_prueba()

        self.assertEqual(manifest.corrida_id, "B-02")
        self.assertEqual(manifest.escenario, "B")
        self.assertEqual(manifest.circuit_breaker.fail_max, 3)
        self.assertEqual(manifest.circuit_breaker.reset_timeout_seconds, 15)
        self.assertEqual(manifest.cache.ttl_seconds, 240)
        self.assertEqual(manifest.mock_openfinance.modo, "lento")
        self.assertEqual(manifest.carga.usuarios, 25)
        self.assertEqual(manifest.carga.duration_seconds, 90)
        self.assertEqual(manifest.carga.spawn_rate, 5.0)
        self.assertEqual(manifest.provider_timeout_ms, 700)

    def test_timestamp_generado_es_timezone_aware_y_utc(self) -> None:
        manifest = construir_manifest(
            ConfiguracionMockOpenFinance(modo="normal"),
            ConfiguracionCarga(usuarios=1, duration_seconds=1),
            fuente_configuracion=_fuente_configuracion(),
        )

        self.assertIsNotNone(manifest.timestamp.tzinfo)
        self.assertEqual(manifest.timestamp.utcoffset(), timedelta(0))

    def test_reutiliza_por_defecto_el_modulo_config_del_adaptador(self) -> None:
        from services.adaptador.app import config as config_adaptador

        with patch.multiple(
            config_adaptador,
            EJECUCION_ID="C-01",
            ESCENARIO="C",
            FAIL_MAX=2,
            RESET_TIMEOUT_S=12,
            TTL_S=180,
            TIMEOUT_MS=650,
            LOG_DIR=None,
        ):
            manifest = construir_manifest(
                ConfiguracionMockOpenFinance(modo="caido"),
                ConfiguracionCarga(usuarios=10, duration_seconds=30),
                timestamp=TIMESTAMP_FIJO,
            )

        self.assertEqual(manifest.corrida_id, "C-01")
        self.assertEqual(manifest.circuit_breaker.fail_max, 2)
        self.assertEqual(manifest.cache.ttl_seconds, 180)

    def test_rechaza_timestamp_naive(self) -> None:
        with self.assertRaisesRegex(ValueError, "zona horaria"):
            construir_manifest(
                ConfiguracionMockOpenFinance(modo="normal"),
                ConfiguracionCarga(usuarios=1, duration_seconds=1),
                fuente_configuracion=_fuente_configuracion(),
                timestamp=datetime(2026, 9, 3, 18, 30),
            )

    def test_rechaza_escenarios_invalidos(self) -> None:
        for escenario in ("H", "a", "N/A", ""):
            with self.subTest(escenario=escenario):
                with self.assertRaises(ValueError):
                    _manifest_prueba(ESCENARIO=escenario)

    def test_rechaza_ids_inseguros_o_no_portables(self) -> None:
        for corrida_id in ("../B-02", "B/02", "B\\02", ".", "CON", "B-02."):
            with self.subTest(corrida_id=corrida_id):
                with self.assertRaises(ValueError):
                    _manifest_prueba(EJECUCION_ID=corrida_id)

    def test_rechaza_parametros_de_infraestructura_no_positivos(self) -> None:
        for campo in ("FAIL_MAX", "RESET_TIMEOUT_S", "TTL_S", "TIMEOUT_MS"):
            with self.subTest(campo=campo):
                with self.assertRaises(ValueError):
                    _manifest_prueba(**{campo: 0})

    def test_rechaza_carga_invalida(self) -> None:
        casos = (
            lambda: ConfiguracionCarga(usuarios=0, duration_seconds=1),
            lambda: ConfiguracionCarga(usuarios=-1, duration_seconds=1),
            lambda: ConfiguracionCarga(usuarios=1, duration_seconds=0),
            lambda: ConfiguracionCarga(usuarios=1, duration_seconds=-1),
            lambda: ConfiguracionCarga(
                usuarios=1, duration_seconds=1, spawn_rate=0
            ),
            lambda: ConfiguracionCarga(
                usuarios=1, duration_seconds=1, spawn_rate=float("inf")
            ),
            lambda: ConfiguracionCarga(
                usuarios=1, duration_seconds=1, spawn_rate=float("nan")
            ),
            lambda: ConfiguracionCarga(usuarios=True, duration_seconds=1),
        )
        for construir in casos:
            with self.subTest(caso=construir):
                with self.assertRaises((TypeError, ValueError)):
                    construir()

    def test_rechaza_modo_invalido(self) -> None:
        with self.assertRaisesRegex(ValueError, "modo debe ser uno de"):
            ConfiguracionMockOpenFinance(modo="slow")

    def test_caida_temporal_exige_autorreparacion_positiva(self) -> None:
        with self.assertRaisesRegex(ValueError, "obligatorio"):
            ConfiguracionMockOpenFinance(modo="caida_temporal")
        with self.assertRaises(ValueError):
            ConfiguracionMockOpenFinance(
                modo="caida_temporal", auto_repair_seconds=0
            )

        configuracion = ConfiguracionMockOpenFinance(
            modo="caida_temporal", auto_repair_seconds=10
        )
        self.assertEqual(configuracion.auto_repair_seconds, 10)

    def test_autorreparacion_no_se_acepta_en_otro_modo(self) -> None:
        with self.assertRaisesRegex(ValueError, "solo aplica"):
            ConfiguracionMockOpenFinance(
                modo="normal", auto_repair_seconds=10
            )

    def test_campos_obligatorios_no_tienen_defaults_ficticios(self) -> None:
        with self.assertRaises(TypeError):
            ConfiguracionCarga(usuarios=10)


class TestSerializacionManifest(unittest.TestCase):
    """Valida el contrato JSON y el tratamiento de opcionales."""

    def test_serializa_json_y_conserva_todos_los_valores(self) -> None:
        datos = json.loads(serializar_manifest(_manifest_prueba()))

        self.assertEqual(
            datos,
            {
                "corrida_id": "B-02",
                "escenario": "B",
                "timestamp": "2026-09-03T18:30:00Z",
                "circuit_breaker": {
                    "fail_max": 3,
                    "reset_timeout_seconds": 15,
                },
                "cache": {"ttl_seconds": 240},
                "mock_openfinance": {"modo": "lento"},
                "carga": {
                    "usuarios": 25,
                    "duration_seconds": 90,
                    "spawn_rate": 5.0,
                },
                "provider_timeout_ms": 700,
            },
        )

    def test_convierte_otro_huso_horario_a_utc_z(self) -> None:
        zona_bogota = timezone(timedelta(hours=-5))
        manifest = construir_manifest(
            ConfiguracionMockOpenFinance(modo="normal"),
            ConfiguracionCarga(usuarios=1, duration_seconds=1),
            fuente_configuracion=_fuente_configuracion(),
            timestamp=datetime(2026, 9, 3, 13, 30, tzinfo=zona_bogota),
        )

        datos = json.loads(serializar_manifest(manifest))
        self.assertEqual(datos["timestamp"], "2026-09-03T18:30:00Z")

    def test_omite_opcionales_no_disponibles(self) -> None:
        manifest = construir_manifest(
            ConfiguracionMockOpenFinance(modo="normal"),
            ConfiguracionCarga(usuarios=1, duration_seconds=1),
            fuente_configuracion=_fuente_configuracion(),
            timestamp=TIMESTAMP_FIJO,
        )

        datos = json.loads(serializar_manifest(manifest))
        self.assertNotIn("auto_repair_seconds", datos["mock_openfinance"])
        self.assertNotIn("spawn_rate", datos["carga"])

    def test_serializa_autorreparacion_cuando_aplica(self) -> None:
        manifest = construir_manifest(
            ConfiguracionMockOpenFinance(
                modo="caida_temporal", auto_repair_seconds=10
            ),
            ConfiguracionCarga(usuarios=1, duration_seconds=1),
            fuente_configuracion=_fuente_configuracion(),
            timestamp=TIMESTAMP_FIJO,
        )

        datos = json.loads(serializar_manifest(manifest))
        self.assertEqual(
            datos["mock_openfinance"],
            {"modo": "caida_temporal", "auto_repair_seconds": 10},
        )

    def test_traduce_error_de_serializacion_y_lo_registra(self) -> None:
        manifest = _manifest_prueba()
        with patch(
            "experimentos.manifest.json.dumps",
            side_effect=TypeError("fallo simulado"),
        ):
            with self.assertLogs("experimentos.manifest", level="ERROR"):
                with self.assertRaises(ErrorSerializacionManifest):
                    serializar_manifest(manifest)


class TestPersistenciaManifest(unittest.TestCase):
    """Valida escritura segura, estructura y colisiones."""

    def test_crea_directorios_y_escribe_manifest_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            with self.assertLogs("experimentos.manifest", level="INFO"):
                ruta = guardar_manifest(
                    _manifest_prueba(), directorio_resultados=temporal
                )

            esperada = (
                Path(temporal).resolve()
                / "escenario_B"
                / "B-02"
                / "manifest.json"
            )
            self.assertEqual(ruta, esperada)
            self.assertEqual(
                json.loads(ruta.read_text(encoding="utf-8"))["corrida_id"],
                "B-02",
            )

    def test_permite_results_csv_hermano_sin_modificarlo(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            directorio_corrida = Path(temporal) / "escenario_B" / "B-02"
            directorio_corrida.mkdir(parents=True)
            resultados = directorio_corrida / "results.csv"
            resultados.write_text("name,count\n/cotizar,1\n", encoding="utf-8")

            ruta = guardar_manifest(
                _manifest_prueba(), directorio_resultados=temporal
            )

            self.assertTrue(ruta.exists())
            self.assertEqual(
                resultados.read_text(encoding="utf-8"),
                "name,count\n/cotizar,1\n",
            )

    def test_no_sobrescribe_manifest_existente(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            manifest = _manifest_prueba()
            ruta = guardar_manifest(manifest, directorio_resultados=temporal)
            contenido_original = ruta.read_text(encoding="utf-8")

            with self.assertLogs("experimentos.manifest", level="ERROR"):
                with self.assertRaises(ManifestExistenteError):
                    guardar_manifest(manifest, directorio_resultados=temporal)

            self.assertEqual(ruta.read_text(encoding="utf-8"), contenido_original)

    def test_rechaza_un_archivo_como_directorio_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "no-es-directorio"
            archivo.write_text("contenido", encoding="utf-8")

            with self.assertLogs("experimentos.manifest", level="ERROR"):
                with self.assertRaises(ErrorPersistenciaManifest):
                    guardar_manifest(
                        _manifest_prueba(), directorio_resultados=archivo
                    )

    def test_rechaza_la_raiz_del_filesystem(self) -> None:
        raiz = Path(Path.cwd().anchor)
        with self.assertRaisesRegex(ValueError, "raiz del filesystem"):
            guardar_manifest(
                _manifest_prueba(), directorio_resultados=raiz
            )

    def test_exige_directorio_si_log_dir_no_existe(self) -> None:
        manifest = _manifest_prueba()
        with self.assertRaisesRegex(ValueError, "LOG_DIR"):
            guardar_manifest(
                manifest,
                fuente_configuracion=_fuente_configuracion(LOG_DIR=None),
            )

    def test_reutiliza_log_dir_de_la_fuente(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            fuente = _fuente_configuracion(LOG_DIR=temporal)
            ruta = guardar_manifest(
                _manifest_prueba(), fuente_configuracion=fuente
            )

            self.assertEqual(
                ruta,
                Path(temporal).resolve()
                / "escenario_B"
                / "B-02"
                / "manifest.json",
            )


if __name__ == "__main__":
    unittest.main()
