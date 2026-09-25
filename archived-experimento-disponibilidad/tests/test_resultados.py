"""Integridad, preservacion y fallos de IO de 5.4, aislados del experimento."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from experimentos.resultados import (
    AdjuntoCSV, AlmacenResultadosLocal, ErrorEntradaResultados,
    ErrorIntegridadResultados, ErrorPersistenciaResultados, ErrorResultados,
    EvidenciaCorrida, PaqueteExistenteError, guardar_resultados, leer_adjunto,
    leer_manifest, leer_registros, validar_paquete,
)
from experimentos.resultados.contratos import COLUMNAS_ADJUNTOS, ValorJSON
from experimentos.resultados.persistencia import _publicar_archivo
from tests.fixtures_resultados import adjunto_prueba, escribir_entradas, registro_prueba


class TestResultados(unittest.TestCase):
    """Verifica contenido y publicacion usando exclusivamente carpetas temporales."""

    def setUp(self) -> None:
        """Crea las dos entradas y una raiz independiente de los resultados historicos."""
        temporal = tempfile.TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.raiz = Path(temporal.name).resolve()
        self.manifest, self.records = escribir_entradas(self.raiz)
        self.salida = self.raiz / "salida"
        self.destino = self.salida / "escenario_B" / "prueba_rep1"
        logger = logging.getLogger("experimentos.resultados")
        nivel, propagar = logger.level, logger.propagate
        handler = logging.NullHandler()
        logger.addHandler(handler)
        logger.propagate = False
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(setattr, logger, "level", nivel)
        self.addCleanup(setattr, logger, "propagate", propagar)

    def _entrada(self, adjuntos: tuple[AdjuntoCSV, ...] = ()) -> EvidenciaCorrida:
        """Carga el contrato mediante los mismos lectores usados por el CLI."""
        return EvidenciaCorrida(leer_manifest(self.manifest), leer_registros(self.records), adjuntos)

    def _guardar(self, adjuntos: tuple[AdjuntoCSV, ...] = ()) -> Path:
        """Invoca el servicio publico sin servicios externos."""
        return guardar_resultados(self._entrada(adjuntos), AlmacenResultadosLocal(self.salida))

    def _filas(self, filas: list[dict[str, ValorJSON]]) -> None:
        """Sustituye solo el archivo temporal de registros del test."""
        self.records.write_text("".join(json.dumps(f) + "\n" for f in filas), encoding="utf-8")

    def _manifest_campo(self, campo: str, valor: object) -> None:
        """Altera un campo del manifest de test para comprobar su rechazo."""
        datos = json.loads(self.manifest.read_bytes())
        datos[campo] = valor
        self.manifest.write_text(json.dumps(datos), encoding="utf-8")

    def test_corrida_valida_crea_estructura_y_revalida(self) -> None:
        ruta = self._guardar()
        self.assertEqual(ruta, self.destino)
        self.assertEqual({p.name for p in ruta.iterdir()}, {"manifest.json", "results.csv", "integridad.json"})
        self.assertEqual((ruta / "manifest.json").read_bytes(), self.manifest.read_bytes())
        evidencia = validar_paquete(ruta)
        self.assertEqual(evidencia.registros.filas, (registro_prueba(),))
        informe = json.loads((ruta / "integridad.json").read_bytes())
        self.assertEqual(informe["cantidad_registros"], 1)
        self.assertEqual(informe["archivos"]["results.csv"], hashlib.sha256((ruta / "results.csv").read_bytes()).hexdigest())

    def test_manifest_inexistente(self) -> None:
        self.manifest.unlink()
        with self.assertRaisesRegex(ErrorEntradaResultados, "archivo regular"):
            self._guardar()
        self.assertFalse(self.salida.exists())

    def test_resultados_inexistentes(self) -> None:
        self.records.unlink()
        with self.assertRaisesRegex(ErrorEntradaResultados, "archivo regular"):
            self._guardar()

    def test_resultados_vacios_y_solo_cabecera(self) -> None:
        for extension, contenido in ((".jsonl", ""), (".csv", ""), (".csv", "request_id,ejecucion_id,escenario\n")):
            with self.subTest(extension=extension, contenido=contenido):
                self.records = self.raiz / f"vacio{extension}"
                self.records.write_text(contenido, encoding="utf-8")
                with self.assertRaises(ErrorResultados):
                    self._guardar()
                self.assertFalse(self.salida.exists())

    def test_escenario_inconsistente(self) -> None:
        self._filas([registro_prueba(escenario="C")])
        with self.assertRaisesRegex(ErrorIntegridadResultados, "escenario no coincide"):
            self._guardar()

    def test_corrida_inconsistente(self) -> None:
        self._filas([registro_prueba(corrida_id="otra_corrida")])
        with self.assertRaisesRegex(ErrorIntegridadResultados, "ejecucion_id no coincide"):
            self._guardar()

    def test_columnas_obligatorias_ausentes(self) -> None:
        for campo in ("request_id", "ejecucion_id", "escenario"):
            with self.subTest(campo=campo):
                fila = registro_prueba()
                del fila[campo]
                self._filas([fila])
                with self.assertRaisesRegex(ErrorIntegridadResultados, campo):
                    self._guardar()

    def test_identidad_vacia_nula_o_no_textual(self) -> None:
        for campo in ("request_id", "ejecucion_id", "escenario"):
            for valor in (None, "", "   ", 1, True):
                with self.subTest(campo=campo, valor=valor):
                    self._filas([{**registro_prueba(), campo: valor}])
                    with self.assertRaisesRegex(ErrorIntegridadResultados, campo):
                        self._guardar()

    def test_varios_registros_misma_corrida_preservan_orden(self) -> None:
        self._filas([registro_prueba("peticion-2"), registro_prueba("peticion-1")])
        evidencia = validar_paquete(self._guardar())
        self.assertEqual([f["request_id"] for f in evidencia.registros.filas], ["peticion-2", "peticion-1"])

    def test_rechaza_mezcla_de_dos_corridas_o_escenarios(self) -> None:
        for segunda in (registro_prueba("otra", "otra_corrida"), registro_prueba("otra", escenario="A")):
            with self.subTest(segunda=segunda):
                self._filas([registro_prueba(), segunda])
                with self.assertRaisesRegex(ErrorIntegridadResultados, "registro 2"):
                    self._guardar()
                self.assertFalse(self.salida.exists())

    def test_misma_corrida_en_dos_escenarios_se_almacena_por_separado(self) -> None:
        primera = self._guardar()
        self._manifest_campo("escenario", "A")
        self._filas([registro_prueba(escenario="A")])
        segunda = self._guardar()
        self.assertNotEqual(primera, segunda)
        self.assertEqual(validar_paquete(primera).manifest.modelo.escenario, "B")
        self.assertEqual(validar_paquete(segunda).manifest.modelo.escenario, "A")

    def test_identificadores_y_escenarios_inseguros(self) -> None:
        for campo, valores in (
            ("corrida_id", ("../escape", "a/b", "a\\b", ".", "CON", "NUL.csv", "run.", "x:ads", "C:\\tmp")),
            ("escenario", ("../A", "H", "a", "N/A", "")),
        ):
            for valor in valores:
                with self.subTest(campo=campo, valor=valor):
                    self.manifest, self.records = escribir_entradas(self.raiz)
                    self._manifest_campo(campo, valor)
                    with self.assertRaises(ErrorEntradaResultados):
                        self._guardar()
                    self.assertFalse(self.salida.exists())

    def test_raiz_invalida(self) -> None:
        for raiz in ("", "   ", self.manifest, Path(self.raiz.anchor)):
            with self.subTest(raiz=raiz), self.assertRaises(ErrorPersistenciaResultados):
                AlmacenResultadosLocal(raiz)

    def test_error_escritura_temporal_preserva_manifest_y_adjuntos(self) -> None:
        self.destino.mkdir(parents=True)
        previo = self.destino / "manifest.json"
        previo.write_bytes(self.manifest.read_bytes())
        adjunto = self.destino / "results_failures.csv"
        contenido = adjunto_prueba(adjunto.name, con_filas=False)
        adjunto.write_bytes(contenido)
        with patch("experimentos.resultados.persistencia._escribir_temporal", side_effect=OSError("disco lleno")):
            with self.assertRaisesRegex(ErrorPersistenciaResultados, "disco lleno"):
                self._guardar()
        self.assertEqual(previo.read_bytes(), self.manifest.read_bytes())
        self.assertEqual(adjunto.read_bytes(), contenido)
        self.assertEqual({p.name for p in self.destino.iterdir()}, {previo.name, adjunto.name})

    def test_fallo_al_publicar_constancia_retira_solo_archivos_propios(self) -> None:
        self.destino.mkdir(parents=True)
        previo = self.destino / "manifest.json"
        previo.write_bytes(self.manifest.read_bytes())

        def publicar(origen: Path, destino: Path) -> None:
            """Inyecta una falla despues de publicar los registros completos."""
            if destino.name == "integridad.json":
                raise OSError("fallo de publicacion final")
            _publicar_archivo(origen, destino)

        with patch("experimentos.resultados.persistencia._publicar_archivo", side_effect=publicar):
            with self.assertRaises(ErrorPersistenciaResultados):
                self._guardar()
        self.assertEqual({p.name for p in self.destino.iterdir()}, {"manifest.json"})

    def test_json_manifest_invalido_duplicado_y_no_finito(self) -> None:
        for texto in ("{", "[]", '{"corrida_id":"a","corrida_id":"b"}', '{"extra":NaN}', '{"extra":1e999}'):
            with self.subTest(texto=texto):
                self.manifest.write_text(texto, encoding="utf-8")
                with self.assertRaisesRegex(ErrorEntradaResultados, "JSON|manifest"):
                    self._guardar()

    def test_manifest_reusa_validaciones_43(self) -> None:
        for campo, valor in (("timestamp", "2026-09-04T20:30:00"), ("cache", {"ttl_seconds": 0}), ("mock_openfinance", {"modo": "SLOW"}), ("carga", {"usuarios": 1})):
            with self.subTest(campo=campo):
                self.manifest, self.records = escribir_entradas(self.raiz)
                self._manifest_campo(campo, valor)
                with self.assertRaises(ErrorEntradaResultados):
                    self._guardar()

    def test_jsonl_invalido_o_no_objeto_no_se_omite(self) -> None:
        valido = json.dumps(registro_prueba())
        for texto in (valido + "\n{\n", valido + "\n\n", "[]\n", '{"a":1,"a":2}\n'):
            with self.subTest(texto=texto):
                self.records.write_text(texto, encoding="utf-8")
                with self.assertRaisesRegex(ErrorEntradaResultados, "linea"):
                    self._guardar()

    def test_rechaza_eventos_de_infraestructura(self) -> None:
        for extra in ({"event_type": "cache_op"}, {"logger": "werkzeug"}, {"logger": "adaptador.cache"}):
            with self.subTest(extra=extra):
                self._filas([{**registro_prueba(), **extra}])
                with self.assertRaises(ErrorIntegridadResultados):
                    self._guardar()

    def test_csv_malformado_cabeceras_y_campos(self) -> None:
        self.records = self.raiz / "requests.csv"
        for texto in (
            "request_id,ejecucion_id,escenario\nx,prueba_rep1\n",
            "request_id,ejecucion_id,escenario\nx,prueba_rep1,B,extra\n",
            'request_id,ejecucion_id,escenario\n"sin cerrar,prueba_rep1,B\n',
            "request_id,ejecucion_id,escenario,escenario\nx,prueba_rep1,B,B\n",
            "request_id,ejecucion_id,escenario,\nx,prueba_rep1,B,x\n",
        ):
            with self.subTest(texto=texto):
                self.records.write_text(texto, encoding="utf-8")
                with self.assertRaisesRegex(ErrorEntradaResultados, "CSV"):
                    self._guardar()

    def test_csv_utf8_bom_comillas_y_saltos_en_celdas(self) -> None:
        self.records = self.raiz / "requests.csv"
        detalle = 'texto, con "comillas"\ny acento: petición'
        with self.records.open("w", encoding="utf-8-sig", newline="") as archivo:
            escritor = csv.writer(archivo)
            escritor.writerow(["escenario", "request_id", "ejecucion_id", "detalle"])
            escritor.writerow(["B", "peticion-1", "prueba_rep1", detalle])
        evidencia = validar_paquete(self._guardar())
        self.assertEqual(evidencia.registros.filas[0]["detalle"], detalle)
        self.assertEqual(evidencia.registros.columnas, ("request_id", "ejecucion_id", "escenario", "detalle"))

    def test_preserva_opcionales_nulos_campos_nuevos_y_manifest_original(self) -> None:
        self._manifest_campo("campo_futuro", {"origen": "test"})
        self._filas([
            {**registro_prueba(), "timestamp_deteccion": None, "proveedor_invocado": False,
             "extra": {"b": [1, True], "a": "á"}, "event_type": "request", "logger": "otro.productor"},
            {**registro_prueba("peticion-2"), "event_type": "request"},
        ])
        evidencia = validar_paquete(self._guardar())
        primera, segunda = evidencia.registros.filas
        self.assertEqual(primera["timestamp_deteccion"], "")
        self.assertEqual(primera["proveedor_invocado"], "false")
        self.assertEqual(json.loads(str(primera["extra"])), {"a": "á", "b": [1, True]})
        self.assertEqual(segunda["extra"], "")
        self.assertEqual(evidencia.manifest.contenido, self.manifest.read_bytes())

    def test_no_sobrescribe_paquete_ni_resultados_preexistentes(self) -> None:
        ruta = self._guardar()
        originales = {p.name: p.read_bytes() for p in ruta.iterdir()}
        with self.assertRaises(PaqueteExistenteError):
            self._guardar()
        self.assertEqual({p.name: p.read_bytes() for p in ruta.iterdir()}, originales)
        (ruta / "integridad.json").unlink()
        with self.assertRaises(PaqueteExistenteError):
            self._guardar()
        self.assertFalse((ruta / "integridad.json").exists())

    def test_manifest_existente_distinto_no_se_sobrescribe(self) -> None:
        self.destino.mkdir(parents=True)
        previo = self.destino / "manifest.json"
        previo.write_bytes(b"otra evidencia")
        with self.assertRaises(PaqueteExistenteError):
            self._guardar()
        self.assertEqual(previo.read_bytes(), b"otra evidencia")
        self.assertEqual({p.name for p in self.destino.iterdir()}, {"manifest.json"})

    def test_adjuntos_opcionales_existentes_y_suministrados(self) -> None:
        self.destino.mkdir(parents=True)
        adjuntos = []
        for nombre in COLUMNAS_ADJUNTOS:
            archivo = self.raiz / nombre
            archivo.write_bytes(adjunto_prueba(nombre, con_filas=nombre not in ("results_failures.csv", "results_exceptions.csv")))
            if nombre == "results_stats.csv":
                (self.destino / nombre).write_bytes(archivo.read_bytes())
            else:
                adjuntos.append(leer_adjunto(archivo))
        evidencia = validar_paquete(self._guardar(tuple(adjuntos)))
        self.assertEqual({a.nombre for a in evidencia.adjuntos}, set(COLUMNAS_ADJUNTOS))
        for adjunto in evidencia.adjuntos:
            self.assertEqual(adjunto.contenido, (self.raiz / adjunto.nombre).read_bytes())

    def test_adjunto_invalido_no_se_certifica(self) -> None:
        for adjunto in (
            AdjuntoCSV("../escape.csv", b"x\n1\n"),
            AdjuntoCSV("results_stats.csv", b"Name\n/cotizar[B]\n"),
            AdjuntoCSV("results_stats.csv", adjunto_prueba("results_stats.csv", con_filas=False)),
            AdjuntoCSV("results_stats.csv", adjunto_prueba("results_stats.csv", escenario="A")),
        ):
            with self.subTest(nombre=adjunto.nombre), self.assertRaises(ErrorResultados):
                self._guardar((adjunto,))
        self.assertFalse(self.salida.exists())

    def test_adjunto_preexistente_invalido_tampoco_se_ignora(self) -> None:
        self.destino.mkdir(parents=True)
        archivo = self.destino / "results_stats.csv"
        archivo.write_bytes(b"Name\n/cotizar[A]\n")
        with self.assertRaises(ErrorResultados):
            self._guardar()
        self.assertEqual(archivo.read_bytes(), b"Name\n/cotizar[A]\n")
        self.assertFalse((self.destino / "integridad.json").exists())

    def test_adjunto_duplicado_o_conflictivo(self) -> None:
        adjunto = AdjuntoCSV("results_failures.csv", adjunto_prueba("results_failures.csv", con_filas=False))
        with self.assertRaisesRegex(ErrorIntegridadResultados, "duplicado"):
            self._guardar((adjunto, adjunto))
        self.destino.mkdir(parents=True)
        (self.destino / adjunto.nombre).write_bytes(adjunto_prueba(adjunto.nombre))
        with self.assertRaises(PaqueteExistenteError):
            self._guardar((adjunto,))

    def test_verificar_rechaza_hash_o_constancia_alterados(self) -> None:
        ruta = self._guardar()
        informe = ruta / "integridad.json"
        original = informe.read_bytes()
        for campo, valor in (("cantidad_registros", 3), ("corrida_id", "otro"), ("version", True), ("archivos", {"../escape": "0" * 64})):
            with self.subTest(campo=campo):
                datos = json.loads(original)
                datos[campo] = valor
                informe.write_text(json.dumps(datos), encoding="utf-8")
                with self.assertRaises(ErrorIntegridadResultados):
                    validar_paquete(ruta)
        informe.write_bytes(original)
        (ruta / "results.csv").write_bytes(b"datos cambiados")
        with self.assertRaisesRegex(ErrorIntegridadResultados, "SHA-256"):
            validar_paquete(ruta)

    def test_verificar_rechaza_archivo_faltante_adjunto_no_inventariado_y_carpeta_distinta(self) -> None:
        ruta = self._guardar()
        nuevo = ruta / "results_failures.csv"
        nuevo.write_bytes(adjunto_prueba(nuevo.name, con_filas=False))
        with self.assertRaisesRegex(ErrorIntegridadResultados, "inventario"):
            validar_paquete(ruta)
        nuevo.unlink()
        otro = ruta.with_name("otro")
        ruta.rename(otro)
        with self.assertRaisesRegex(ErrorIntegridadResultados, "carpetas"):
            validar_paquete(otro)
        (otro / "manifest.json").unlink()
        with self.assertRaisesRegex(ErrorIntegridadResultados, "inventario"):
            validar_paquete(otro)

    def test_verificar_sin_constancia_no_acepta_paquete(self) -> None:
        ruta = self._guardar()
        (ruta / "integridad.json").unlink()
        with self.assertRaises(ErrorResultados):
            validar_paquete(ruta)

    def test_lock_existente_no_se_elimina(self) -> None:
        self.destino.mkdir(parents=True)
        lock = self.destino / ".guardar-resultados.lock"
        lock.write_bytes(b"otro proceso")
        with self.assertRaises(PaqueteExistenteError):
            self._guardar()
        self.assertEqual(lock.read_bytes(), b"otro proceso")

    def test_colision_durante_publicacion_no_sobrescribe_archivo_ajeno(self) -> None:
        def publicar(origen: Path, destino: Path) -> None:
            """Crea un archivo competidor justo antes del enlace exclusivo."""
            if destino.name == "results.csv":
                destino.write_bytes(b"evidencia ajena")
            _publicar_archivo(origen, destino)

        with patch("experimentos.resultados.persistencia._publicar_archivo", side_effect=publicar):
            with self.assertRaises(PaqueteExistenteError):
                self._guardar()
        self.assertEqual({p.name for p in self.destino.iterdir()}, {"results.csv"})
        self.assertEqual((self.destino / "results.csv").read_bytes(), b"evidencia ajena")

    def test_rechaza_symlink_de_escenario_y_de_manifest(self) -> None:
        externo = self.raiz / "externo"
        externo.mkdir()
        self.salida.mkdir()
        enlace = self.salida / "escenario_B"
        try:
            enlace.symlink_to(externo, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"el sistema no permite crear symlinks: {error}")
        with self.assertRaises(ErrorPersistenciaResultados):
            self._guardar()
        self.assertEqual(list(externo.iterdir()), [])
        enlace.unlink()
        self.destino.mkdir(parents=True)
        (self.destino / "manifest.json").symlink_to(self.manifest)
        with self.assertRaises(ErrorPersistenciaResultados):
            self._guardar()

    def test_escritura_determinista(self) -> None:
        primero = self._guardar()
        segunda_raiz = self.raiz / "segunda"
        segundo = guardar_resultados(self._entrada(), AlmacenResultadosLocal(segunda_raiz))
        self.assertEqual({p.name: p.read_bytes() for p in primero.iterdir()}, {p.name: p.read_bytes() for p in segundo.iterdir()})

    def test_almacen_sustituible_y_validacion_previa(self) -> None:
        almacen = Mock()
        almacen.guardar.return_value = self.raiz / "destino-alternativo"
        evidencia = self._entrada()
        self.assertEqual(guardar_resultados(evidencia, almacen), almacen.guardar.return_value)
        almacen.guardar.assert_called_once_with(evidencia)
        self._filas([registro_prueba(corrida_id="otra")])
        almacen.reset_mock()
        with self.assertRaises(ErrorIntegridadResultados):
            guardar_resultados(self._entrada(), almacen)
        almacen.guardar.assert_not_called()

    def test_dto_distinto_del_json_no_se_publica(self) -> None:
        evidencia = self._entrada()
        falso = replace(evidencia.manifest, modelo=replace(evidencia.manifest.modelo, corrida_id="otra"))
        with self.assertRaisesRegex(ErrorIntegridadResultados, "DTO"):
            guardar_resultados(replace(evidencia, manifest=falso), AlmacenResultadosLocal(self.salida))

    def test_errores_de_lectura_y_encoding_son_descriptivos(self) -> None:
        with patch("pathlib.Path.read_bytes", side_effect=PermissionError("sin permiso")):
            with self.assertRaisesRegex(ErrorEntradaResultados, "sin permiso"):
                leer_registros(self.records)
        self.records.write_bytes(b"\xff")
        with self.assertRaisesRegex(ErrorEntradaResultados, "UTF-8"):
            leer_registros(self.records)


if __name__ == "__main__":
    unittest.main()
