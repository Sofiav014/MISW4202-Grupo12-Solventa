"""Pruebas del empaquetado 5.4 desde el productor real de registros JSONL."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from experimentos.resultados import (
    AlmacenResultadosLocal, ErrorEntradaResultados, ErrorIntegridadResultados,
    guardar_resultados, leer_log_compartido, leer_manifest,
    preparar_corridas_desde_log, validar_paquete,
)
from experimentos.resultados.contratos import COLUMNAS_ADJUNTOS, ValorJSON
from tests.fixtures_resultados import adjunto_prueba, manifest_prueba, registro_prueba

CLI = Path(__file__).resolve().parents[1] / "load-testing" / "run_escenario.py"


class TestLogCompartido(unittest.TestCase):
    """Fixtures minimos de archivos; no ejecuta Locust ni fabrica medidas."""

    def setUp(self) -> None:
        """Crea manifests A/B que comparten corrida_id y un JSONL comun."""
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name).resolve()
        self.log = self.raiz / "adaptador.jsonl"
        self.carpetas: dict[str, Path] = {}
        for escenario in ("A", "B"):
            carpeta = self.raiz / f"escenario_{escenario}" / "prueba_rep1"
            carpeta.mkdir(parents=True)
            (carpeta / "manifest.json").write_text(
                json.dumps(manifest_prueba(escenario=escenario)), encoding="utf-8",
            )
            self.carpetas[escenario] = carpeta
        self._escribir([
            self._peticion("B", "b-antes", "2026-09-04T20:29:59Z"),
            {"logger": "werkzeug", "level": "INFO"},
            self._peticion("A", "a-1", "2026-09-04T20:30:02Z"),
            {"event_type": "cache_op", "request_id": "b-despues", "ejecucion_id": "prueba_rep1"},
            self._peticion("B", "b-despues", "2026-09-04T20:30:01Z"),
        ])

    def _peticion(self, escenario: str, request_id: str, instante: str) -> dict[str, ValorJSON]:
        """Representa el contrato del logger sin producir metricas de prueba."""
        return {
            **registro_prueba(request_id, escenario=escenario),
            "event_type": "request", "logger": "adaptador.request", "ts_wall": instante,
        }

    def _escribir(self, eventos: list[dict[str, ValorJSON]]) -> None:
        """Escribe unicamente el log temporal propiedad de la prueba."""
        self.log.write_text(
            "".join(json.dumps(evento, ensure_ascii=False) + "\n" for evento in eventos),
            encoding="utf-8",
        )

    def _cli(self, *argumentos: str) -> subprocess.CompletedProcess[str]:
        """Invoca 5.4 en otro proceso, sin paquetes de infraestructura."""
        return subprocess.run(
            [sys.executable, "-S", str(CLI), "guardar-resultados", *argumentos],
            cwd=self.raiz, text=True, encoding="utf-8", capture_output=True,
            timeout=20, check=False,
        )

    def test_separa_identidad_compuesta_y_preserva_orden(self) -> None:
        fuente = leer_log_compartido(self.log)
        self.assertEqual(set(fuente.grupos), {("A", "prueba_rep1"), ("B", "prueba_rep1")})
        self.assertEqual(
            [fila["request_id"] for fila in fuente.grupos[("B", "prueba_rep1")].filas],
            ["b-antes", "b-despues"],
        )
        self.assertEqual(fuente.procedencia.sha256_fuente, hashlib.sha256(self.log.read_bytes()).hexdigest())

    def test_sin_ventana_confirmada_conserva_antes_y_despues_del_manifest(self) -> None:
        evidencias = preparar_corridas_desde_log(self.raiz, self.log)
        self.assertEqual([e.manifest.modelo.escenario for e in evidencias], ["A", "B"])
        evidencia = evidencias[1]
        self.assertEqual(len(evidencia.registros.filas), 2)
        self.assertFalse(evidencia.procedencia.ventana_medicion_confirmada)
        self.assertIn("warm-up", " ".join(evidencia.procedencia.limitaciones))

    def test_sin_ts_wall_tampoco_bloquea_empaquetado(self) -> None:
        self._escribir([{**registro_prueba(), "event_type": "request", "timestamp_inicio": 1}])
        fuente = leer_log_compartido(self.log)
        evidencia = fuente.evidencia(leer_manifest(self.carpetas["B"] / "manifest.json"))
        with self.assertLogs("experimentos.resultados", level="INFO"):
            ruta = guardar_resultados(evidencia, AlmacenResultadosLocal(self.raiz))
        self.assertEqual(len(validar_paquete(ruta).registros.filas), 1)

    def test_registro_con_identidad_incompleta_falla(self) -> None:
        for campo in ("request_id", "ejecucion_id", "escenario"):
            with self.subTest(campo=campo):
                peticion = self._peticion("B", "b-1", "2026-09-04T20:30:01Z")
                del peticion[campo]
                self._escribir([peticion])
                with self.assertRaisesRegex(ErrorIntegridadResultados, campo):
                    leer_log_compartido(self.log)

    def test_peticion_sin_clasificacion_no_se_descarta(self) -> None:
        for dato in (registro_prueba(), {**registro_prueba(), "logger": "adaptador.request"}):
            with self.subTest(dato=dato):
                self._escribir([dato])
                with self.assertRaisesRegex(ErrorIntegridadResultados, "event_type"):
                    leer_log_compartido(self.log)

    def test_log_invalido_aun_en_evento_auxiliar_falla(self) -> None:
        with self.log.open("a", encoding="utf-8") as archivo:
            archivo.write('{"logger": "werkzeug",\n')
        with self.assertRaisesRegex(ErrorEntradaResultados, "linea 6"):
            leer_log_compartido(self.log)

    def test_log_sin_peticiones_o_inexistente(self) -> None:
        for eventos in ([], [{"logger": "werkzeug"}]):
            with self.subTest(eventos=eventos):
                self._escribir(eventos)
                with self.assertRaisesRegex(ErrorIntegridadResultados, "sin peticiones"):
                    leer_log_compartido(self.log)
        self.log.unlink()
        with self.assertRaises(ErrorEntradaResultados):
            leer_log_compartido(self.log)

    def test_identidad_sin_manifest_y_manifest_sin_registros_fallan(self) -> None:
        self._escribir([self._peticion("C", "c-1", "2026-09-04T20:30:01Z")])
        with self.assertRaisesRegex(ErrorIntegridadResultados, "sin manifest"):
            preparar_corridas_desde_log(self.raiz, self.log)
        self._escribir([self._peticion("A", "a-1", "2026-09-04T20:30:01Z")])
        with self.assertRaisesRegex(ErrorIntegridadResultados, "sin peticiones"):
            preparar_corridas_desde_log(self.raiz, self.log)

    def test_manifest_inexistente_en_una_carpeta_no_se_omite(self) -> None:
        (self.carpetas["B"] / "manifest.json").unlink()
        with self.assertRaises(ErrorEntradaResultados):
            preparar_corridas_desde_log(self.raiz, self.log)

    def test_cli_empaqueta_todas_las_corridas_y_preserva_originales(self) -> None:
        for escenario, carpeta in self.carpetas.items():
            for nombre in COLUMNAS_ADJUNTOS:
                (carpeta / nombre).write_bytes(adjunto_prueba(
                    nombre, escenario, con_filas=nombre not in ("results_failures.csv", "results_exceptions.csv"),
                ))
        originales = {p: p.read_bytes() for p in self.raiz.rglob("*") if p.is_file()}
        resultado = self._cli(
            "--manifests-dir", str(self.raiz), "--log-compartido", str(self.log),
            "--resultados-dir", str(self.raiz),
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(resultado.stdout.splitlines(), [str(self.carpetas[e]) for e in ("A", "B")])
        for escenario, carpeta in self.carpetas.items():
            evidencia = validar_paquete(carpeta)
            self.assertEqual({f["escenario"] for f in evidencia.registros.filas}, {escenario})
            self.assertEqual(len(evidencia.adjuntos), 4)
            self.assertFalse(evidencia.procedencia.ventana_medicion_confirmada)
            self.assertIn("procedencia.json", json.loads((carpeta / "integridad.json").read_bytes())["archivos"])
        for archivo, contenido in originales.items():
            self.assertEqual(archivo.read_bytes(), contenido)
        self.assertIn("results_measurement_window_unconfirmed", resultado.stderr)

    def test_cli_una_corrida_desde_log_y_colision(self) -> None:
        argumentos = (
            "--manifest", str(self.carpetas["B"] / "manifest.json"),
            "--log-compartido", str(self.log), "--resultados-dir", str(self.raiz),
        )
        resultado = self._cli(*argumentos)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertFalse((self.carpetas["A"] / "results.csv").exists())
        self.assertEqual(self._cli(*argumentos).returncode, 1)

    def test_cli_prevalida_lote_completo_antes_de_publicar(self) -> None:
        (self.carpetas["B"] / "manifest.json").write_text("{", encoding="utf-8")
        resultado = self._cli(
            "--manifests-dir", str(self.raiz), "--log-compartido", str(self.log),
            "--resultados-dir", str(self.raiz),
        )
        self.assertEqual(resultado.returncode, 1)
        self.assertFalse(list(self.raiz.rglob("results.csv")))
        self.assertFalse(list(self.raiz.rglob("integridad.json")))

    def test_cli_rechaza_combinacion_incompatible(self) -> None:
        resultado = self._cli("--manifests-dir", str(self.raiz), "--records", str(self.log))
        self.assertEqual(resultado.returncode, 2)

    def test_procedencia_alterada_falla_verificacion(self) -> None:
        evidencia = leer_log_compartido(self.log).evidencia(leer_manifest(self.carpetas["B"] / "manifest.json"))
        with self.assertLogs("experimentos.resultados", level="INFO"):
            ruta = guardar_resultados(evidencia, AlmacenResultadosLocal(self.raiz))
        (ruta / "procedencia.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ErrorIntegridadResultados, "SHA-256"):
            validar_paquete(ruta)

    def test_separador_unicode_en_string_no_divide_el_json(self) -> None:
        self._escribir([{**registro_prueba(), "event_type": "request", "detalle": "uno\u2028dos"}])
        fuente = leer_log_compartido(self.log)
        self.assertEqual(fuente.grupos[("B", "prueba_rep1")].filas[0]["detalle"], "uno\u2028dos")


if __name__ == "__main__":
    unittest.main()
