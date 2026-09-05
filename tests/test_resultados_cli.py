"""Contrato del CLI 5.4 en procesos sin paquetes externos ni servicios vivos."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from experimentos.resultados import validar_paquete
from experimentos.resultados.cli import main
from tests.fixtures_resultados import adjunto_prueba, escribir_entradas

RAIZ = Path(__file__).resolve().parents[1]
CLI = RAIZ / "load-testing" / "run_escenario.py"


class TestResultadosCLI(unittest.TestCase):
    """Ejecuta el punto de entrada real con -S, deshabilitando site-packages."""

    def setUp(self) -> None:
        """Aisla todas las entradas y salidas fuera del checkout."""
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name).resolve()
        self.manifest, self.records = escribir_entradas(self.raiz)
        self.salida = self.raiz / "salida"

    def _ejecutar(self, *argumentos: str) -> subprocess.CompletedProcess[str]:
        """Invoca unicamente guardar-resultados; nunca genera carga ni usa Docker."""
        return subprocess.run(
            [sys.executable, "-S", str(CLI), "guardar-resultados", *argumentos],
            cwd=self.raiz, capture_output=True, text=True, encoding="utf-8", timeout=15,
            check=False,
        )

    def _argumentos(self) -> list[str]:
        """Entrega las rutas temporales del caso actual."""
        return ["--manifest", str(self.manifest), "--records", str(self.records),
                "--resultados-dir", str(self.salida)]

    def test_exito_sin_dependencias_externas_muestra_ruta_y_codigo_cero(self) -> None:
        resultado = self._ejecutar(*self._argumentos())
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        destino = self.salida / "escenario_B" / "prueba_rep1"
        self.assertEqual(resultado.stdout.strip(), str(destino))
        self.assertEqual(len(validar_paquete(destino).registros.filas), 1)
        eventos = [json.loads(linea) for linea in resultado.stderr.splitlines()]
        self.assertEqual(eventos[-1]["event_type"], "results_saved")
        self.assertEqual(eventos[-1]["corrida_id"], "prueba_rep1")

    def test_entrada_csv_y_adjuntos_repetibles(self) -> None:
        self.records = self.raiz / "requests.csv"
        self.records.write_text("request_id,ejecucion_id,escenario\np-1,prueba_rep1,B\n", encoding="utf-8")
        argumentos = self._argumentos()
        for nombre in ("results_failures.csv", "results_exceptions.csv"):
            archivo = self.raiz / nombre
            archivo.write_bytes(adjunto_prueba(nombre, con_filas=False))
            argumentos.extend(["--adjunto", str(archivo)])
        resultado = self._ejecutar(*argumentos)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(len(validar_paquete(Path(resultado.stdout.strip())).adjuntos), 2)

    def test_ayuda_no_requiere_escenario_ni_infraestructura(self) -> None:
        resultado = self._ejecutar("--help")
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertIn("--records", resultado.stdout)
        self.assertIn("--adjunto", resultado.stdout)
        self.assertFalse(self.salida.exists())

    def test_argumentos_ausentes_devuelven_dos(self) -> None:
        resultado = self._ejecutar()
        self.assertEqual(resultado.returncode, 2)
        self.assertIn("--manifest", resultado.stderr)
        self.assertFalse(self.salida.exists())

    def test_manifest_inexistente_devuelve_uno_y_error_descriptivo(self) -> None:
        self.manifest.unlink()
        resultado = self._ejecutar(*self._argumentos())
        self.assertEqual(resultado.returncode, 1)
        self.assertEqual(resultado.stdout, "")
        self.assertIn("manifest.json", json.loads(resultado.stderr)["message"])
        self.assertNotIn("Traceback", resultado.stderr)

    def test_error_de_io_devuelve_uno(self) -> None:
        self.salida.write_text("esto es un archivo", encoding="utf-8")
        resultado = self._ejecutar(*self._argumentos())
        self.assertEqual(resultado.returncode, 1)
        self.assertEqual(self.salida.read_text(encoding="utf-8"), "esto es un archivo")

    def test_corrida_inconsistente_devuelve_uno_y_no_publica(self) -> None:
        self.records.write_text('{"request_id":"x","ejecucion_id":"otra","escenario":"B"}\n', encoding="utf-8")
        resultado = self._ejecutar(*self._argumentos())
        self.assertEqual(resultado.returncode, 1)
        self.assertIn("ejecucion_id", resultado.stderr)
        self.assertFalse(self.salida.exists())

    def test_colision_devuelve_uno_y_preserva_bytes(self) -> None:
        primero = self._ejecutar(*self._argumentos())
        self.assertEqual(primero.returncode, 0, primero.stderr)
        destino = Path(primero.stdout.strip())
        originales = {p.name: p.read_bytes() for p in destino.iterdir()}
        segundo = self._ejecutar(*self._argumentos())
        self.assertEqual(segundo.returncode, 1)
        self.assertEqual({p.name: p.read_bytes() for p in destino.iterdir()}, originales)

    def test_raiz_predeterminada_del_repo_no_consulta_log_dir(self) -> None:
        almacen = Mock()
        almacen.guardar.return_value = self.raiz / "destino-falso"
        # El fake evita escribir en la raiz real mientras comprueba el default.
        with patch("experimentos.resultados.cli.AlmacenResultadosLocal", return_value=almacen) as fabrica:
            with patch.dict("os.environ", {"LOG_DIR": "no-usar-contenedor"}):
                with patch("builtins.print"):
                    codigo = main(["--manifest", str(self.manifest), "--records", str(self.records)])
        self.assertEqual(codigo, 0)
        fabrica.assert_called_once_with(RAIZ / "resultados")

    def test_parser_anterior_conserva_sus_argumentos(self) -> None:
        modulo = runpy.run_path(str(CLI), run_name="prueba_cli")
        control = SimpleNamespace(JOURNEY_URL="http://test.invalid")
        with patch.dict(sys.modules, {"control": control}):
            with patch.object(sys, "argv", [str(CLI), "--escenario", "A,B", "--repeticiones", "3", "--ejecucion-id", "suite1"]):
                args = modulo["parse_args"]()
        self.assertEqual((args.escenario, args.repeticiones, args.ejecucion_id), ("A,B", 3, "suite1"))
        self.assertEqual(args.journey_url, "http://test.invalid")


if __name__ == "__main__":
    unittest.main()
