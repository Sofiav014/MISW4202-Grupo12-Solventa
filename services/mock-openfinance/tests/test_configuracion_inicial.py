import json
import os
import subprocess
import sys
import unittest


class ConfiguracionInicialTest(unittest.TestCase):
    SCRIPT_CONFIGURACION_ACTUAL = """
import json
from app.main import motor_comportamiento

configuracion = motor_comportamiento.configuracionActual
print(json.dumps({
    "latenciaMinMs": configuracion.latenciaMinMs,
    "latenciaMaxMs": configuracion.latenciaMaxMs,
    "tasaError": configuracion.tasaError,
    "autoRepararSegundos": configuracion.autoRepararSegundos,
}))
"""

    @classmethod
    def _iniciar_aplicacion(cls, modo, auto_reparar_segundos=None):
        entorno = os.environ.copy()
        if modo is None:
            entorno.pop("MODO", None)
        else:
            entorno["MODO"] = modo
        if auto_reparar_segundos is None:
            entorno.pop("AUTO_REPARAR_SEGUNDOS", None)
        else:
            entorno["AUTO_REPARAR_SEGUNDOS"] = auto_reparar_segundos

        return subprocess.run(
            [sys.executable, "-c", cls.SCRIPT_CONFIGURACION_ACTUAL],
            capture_output=True,
            env=entorno,
            text=True,
            check=False,
        )

    def test_inicia_con_el_perfil_configurado_en_modo(self):
        casos = (
            ("normal", None, {
                "latenciaMinMs": 50,
                "latenciaMaxMs": 100,
                "tasaError": 0.0,
                "autoRepararSegundos": None,
            }),
            ("lento", None, {
                "latenciaMinMs": 900,
                "latenciaMaxMs": 1200,
                "tasaError": 0.0,
                "autoRepararSegundos": None,
            }),
            ("caido", None, {
                "latenciaMinMs": 10,
                "latenciaMaxMs": 30,
                "tasaError": 1.0,
                "autoRepararSegundos": None,
            }),
            ("caida_temporal", "15", {
                "latenciaMinMs": 10,
                "latenciaMaxMs": 30,
                "tasaError": 1.0,
                "autoRepararSegundos": 15,
            }),
        )

        for modo, segundos, configuracion_esperada in casos:
            with self.subTest(modo=modo):
                resultado = self._iniciar_aplicacion(modo, segundos)

                self.assertEqual(resultado.returncode, 0, resultado.stderr)
                self.assertEqual(
                    json.loads(resultado.stdout),
                    configuracion_esperada,
                )

    def test_inicia_en_normal_cuando_modo_no_esta_definido(self):
        resultado = self._iniciar_aplicacion(None)

        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(
            json.loads(resultado.stdout),
            {
                "latenciaMinMs": 50,
                "latenciaMaxMs": 100,
                "tasaError": 0.0,
                "autoRepararSegundos": None,
            },
        )

    def test_rechaza_modo_invalido_o_vacio_al_iniciar(self):
        for modo in ("NORMAL", "SLOW", "DOWN", "DESCONOCIDO", ""):
            with self.subTest(modo=modo):
                resultado = self._iniciar_aplicacion(modo)

                self.assertNotEqual(resultado.returncode, 0)
                self.assertIn(
                    "MODO debe ser uno de: normal, lento, caido, caida_temporal",
                    resultado.stderr,
                )

    def test_caida_temporal_requiere_una_duracion_valida(self):
        for segundos in (None, "", "texto", "1.5", "0", "-1"):
            with self.subTest(segundos=segundos):
                resultado = self._iniciar_aplicacion(
                    "caida_temporal",
                    segundos,
                )

                self.assertNotEqual(resultado.returncode, 0)
                self.assertIn("AUTO_REPARAR_SEGUNDOS", resultado.stderr)


if __name__ == "__main__":
    unittest.main()
