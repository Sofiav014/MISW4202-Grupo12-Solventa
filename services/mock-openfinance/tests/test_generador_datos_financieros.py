import unittest
from datetime import datetime, timezone

from app.generador_datos_financieros import GeneradorDatosFinancieros
from app.main import app


CAMPOS_PERFIL = {
    "cliente_id",
    "score_riesgo",
    "fuente",
    "timestamp_perfil",
}


def parsear_timestamp_utc(timestamp_perfil):
    return datetime.fromisoformat(timestamp_perfil.replace("Z", "+00:00"))


class GeneradorDatosFinancierosTest(unittest.TestCase):
    def setUp(self):
        self.generador = GeneradorDatosFinancieros()

    def test_genera_el_mismo_score_para_el_mismo_cliente(self):
        perfil_inicial = self.generador.obtenerPerfil("cliente-123")
        perfil_nueva_instancia = GeneradorDatosFinancieros().obtenerPerfil(
            "cliente-123"
        )

        self.assertEqual(
            perfil_inicial["score_riesgo"],
            perfil_nueva_instancia["score_riesgo"],
        )

    def test_genera_scores_diferentes_para_clientes_diferentes(self):
        primer_perfil = self.generador.obtenerPerfil("cliente-123")
        segundo_perfil = self.generador.obtenerPerfil("cliente-456")

        self.assertNotEqual(
            primer_perfil["score_riesgo"],
            segundo_perfil["score_riesgo"],
        )

    def test_respeta_el_contrato_rango_y_timestamp(self):
        instante_anterior = datetime.now(timezone.utc)
        perfil = self.generador.obtenerPerfil("cliente-123")
        instante_posterior = datetime.now(timezone.utc)

        self.assertEqual(set(perfil), CAMPOS_PERFIL)
        self.assertEqual(perfil["cliente_id"], "cliente-123")
        self.assertIsInstance(perfil["score_riesgo"], int)
        self.assertGreaterEqual(perfil["score_riesgo"], 0)
        self.assertLessEqual(perfil["score_riesgo"], 1000)
        self.assertEqual(perfil["fuente"], "OPEN_FINANCE")
        self.assertIsInstance(perfil["timestamp_perfil"], str)
        self.assertTrue(perfil["timestamp_perfil"].endswith("Z"))

        timestamp_perfil = parsear_timestamp_utc(perfil["timestamp_perfil"])
        self.assertEqual(timestamp_perfil.tzinfo, timezone.utc)
        self.assertGreaterEqual(timestamp_perfil, instante_anterior)
        self.assertLessEqual(timestamp_perfil, instante_posterior)

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

        perfil = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(perfil), CAMPOS_PERFIL)
        self.assertEqual(perfil["cliente_id"], cliente_id)
        self.assertEqual(perfil["fuente"], "OPEN_FINANCE")
        parsear_timestamp_utc(perfil["timestamp_perfil"])


if __name__ == "__main__":
    unittest.main()
