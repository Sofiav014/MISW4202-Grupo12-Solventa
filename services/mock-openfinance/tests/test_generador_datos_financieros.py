import unittest
from datetime import date

from app.generador_datos_financieros import GeneradorDatosFinancieros
from app.main import app


CAMPOS_PERFIL = {
    "clienteId",
    "puntajeEstabilidadIngresos",
    "relacionDeudaIngresos",
    "puntajeComportamientoPago",
    "incumplimientos12Meses",
    "periodoInformacion",
    "fechaVigenciaDatos",
}


class GeneradorDatosFinancierosTest(unittest.TestCase):
    def setUp(self):
        self.generador = GeneradorDatosFinancieros()

    def test_genera_el_mismo_perfil_para_el_mismo_cliente(self):
        perfil_inicial = self.generador.obtenerPerfil("cliente-123")
        perfil_nueva_instancia = GeneradorDatosFinancieros().obtenerPerfil(
            "cliente-123"
        )

        self.assertEqual(perfil_inicial, perfil_nueva_instancia)

    def test_genera_perfiles_diferentes_para_clientes_diferentes(self):
        primer_perfil = self.generador.obtenerPerfil("cliente-123")
        segundo_perfil = self.generador.obtenerPerfil("cliente-456")

        self.assertNotEqual(primer_perfil, segundo_perfil)

    def test_respeta_el_contrato_rangos_y_fechas(self):
        perfil = self.generador.obtenerPerfil("cliente-123")

        self.assertEqual(set(perfil), CAMPOS_PERFIL)
        self.assertEqual(perfil["clienteId"], "cliente-123")
        self.assertIsInstance(perfil["puntajeEstabilidadIngresos"], int)
        self.assertIsInstance(perfil["relacionDeudaIngresos"], float)
        self.assertIsInstance(perfil["puntajeComportamientoPago"], int)
        self.assertIsInstance(perfil["incumplimientos12Meses"], int)
        self.assertGreaterEqual(perfil["puntajeEstabilidadIngresos"], 0)
        self.assertLessEqual(perfil["puntajeEstabilidadIngresos"], 100)
        self.assertGreaterEqual(perfil["relacionDeudaIngresos"], 0.0)
        self.assertLessEqual(perfil["relacionDeudaIngresos"], 1.0)
        self.assertGreaterEqual(perfil["puntajeComportamientoPago"], 0)
        self.assertLessEqual(perfil["puntajeComportamientoPago"], 100)
        self.assertGreaterEqual(perfil["incumplimientos12Meses"], 0)
        self.assertLessEqual(perfil["incumplimientos12Meses"], 5)
        self.assertEqual(perfil["periodoInformacion"], "2025-01-01/2025-12-31")
        self.assertEqual(perfil["fechaVigenciaDatos"], "2026-12-31")

        inicio_periodo, fin_periodo = perfil["periodoInformacion"].split("/")
        date.fromisoformat(inicio_periodo)
        date.fromisoformat(fin_periodo)
        date.fromisoformat(perfil["fechaVigenciaDatos"])

    def test_mantiene_coherencia_entre_pago_e_incumplimientos(self):
        for cliente_id in ("cliente-1", "cliente-2", "cliente-3", "cliente-4"):
            with self.subTest(cliente_id=cliente_id):
                perfil = self.generador.obtenerPerfil(cliente_id)
                maximo = self.generador._maximo_incumplimientos(
                    perfil["puntajeComportamientoPago"]
                )
                self.assertLessEqual(perfil["incumplimientos12Meses"], maximo)

    def test_rechaza_identificadores_invalidos(self):
        for cliente_id in (None, 123, object()):
            with self.subTest(cliente_id=cliente_id):
                with self.assertRaisesRegex(TypeError, "debe ser un String"):
                    self.generador.obtenerPerfil(cliente_id)

        for cliente_id in ("", "   ", "\t\n"):
            with self.subTest(cliente_id=repr(cliente_id)):
                with self.assertRaisesRegex(ValueError, "no puede estar vacío"):
                    self.generador.obtenerPerfil(cliente_id)


class PerfilEndpointTest(unittest.TestCase):
    def test_responde_exclusivamente_el_dto_financiero(self):
        cliente_id = "cliente-123"

        with app.test_client() as client:
            response = client.get(f"/perfil/{cliente_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.get_json()), CAMPOS_PERFIL)
        self.assertEqual(
            response.get_json(),
            GeneradorDatosFinancieros().obtenerPerfil(cliente_id),
        )


if __name__ == "__main__":
    unittest.main()
