import unittest
from dataclasses import FrozenInstanceError
from threading import Event, Thread
from unittest.mock import patch

from app.main import app, motor_comportamiento
from app.motor_comportamiento import (
    ConfiguracionComportamientoDTO,
    FabricaModosOpenFinance,
    MotorComportamiento,
)


def crear_configuracion(
    latencia_min=0,
    latencia_max=0,
    tasa_error=0.0,
    auto_reparar_segundos=None,
):
    return ConfiguracionComportamientoDTO(
        latenciaMinMs=latencia_min,
        latenciaMaxMs=latencia_max,
        tasaError=tasa_error,
        autoRepararSegundos=auto_reparar_segundos,
    )


def serializar_configuracion(configuracion):
    return {
        "latenciaMinMs": configuracion.latenciaMinMs,
        "latenciaMaxMs": configuracion.latenciaMaxMs,
        "tasaError": configuracion.tasaError,
        "autoRepararSegundos": configuracion.autoRepararSegundos,
    }


class ConfiguracionComportamientoDTOTest(unittest.TestCase):
    def test_es_inmutable_y_autoreparacion_es_opcional(self):
        configuracion = crear_configuracion()

        self.assertIsNone(configuracion.autoRepararSegundos)
        with self.assertRaises(FrozenInstanceError):
            configuracion.tasaError = 0.5

    def test_rechaza_configuraciones_invalidas(self):
        configuracion_base = {
            "latenciaMinMs": 0,
            "latenciaMaxMs": 100,
            "tasaError": 0.0,
            "autoRepararSegundos": None,
        }
        casos = (
            ({"latenciaMinMs": True}, TypeError),
            ({"latenciaMaxMs": 1.5}, TypeError),
            ({"latenciaMinMs": -1}, ValueError),
            ({"latenciaMinMs": 101}, ValueError),
            ({"tasaError": "0.5"}, TypeError),
            ({"tasaError": -0.1}, ValueError),
            ({"tasaError": 1.1}, ValueError),
            ({"autoRepararSegundos": True}, TypeError),
            ({"autoRepararSegundos": 1.5}, TypeError),
            ({"autoRepararSegundos": 0}, ValueError),
            ({"autoRepararSegundos": -1}, ValueError),
        )

        for cambios, error_esperado in casos:
            with self.subTest(cambios=cambios):
                datos = configuracion_base | cambios
                with self.assertRaises(error_esperado):
                    ConfiguracionComportamientoDTO(**datos)


class FabricaModosOpenFinanceTest(unittest.TestCase):
    def test_construye_los_modos_predefinidos(self):
        casos = (
            (
                FabricaModosOpenFinance.modo_normal,
                crear_configuracion(50, 100, 0.0),
            ),
            (
                FabricaModosOpenFinance.modo_lento,
                crear_configuracion(900, 1200, 0.0),
            ),
            (
                FabricaModosOpenFinance.modo_caido,
                crear_configuracion(10, 30, 1.0),
            ),
        )

        for fabrica, configuracion_esperada in casos:
            with self.subTest(fabrica=fabrica.__name__):
                self.assertEqual(fabrica(), configuracion_esperada)

    def test_construye_la_caida_temporal(self):
        self.assertEqual(
            FabricaModosOpenFinance.modo_caida_temporal(15),
            crear_configuracion(10, 30, 1.0, 15),
        )


class MotorComportamientoTest(unittest.TestCase):
    def test_inicia_en_modo_normal(self):
        configuracion = MotorComportamiento().configuracionActual

        self.assertEqual(
            configuracion,
            FabricaModosOpenFinance.modo_normal(),
        )
        with self.assertRaises(TypeError):
            MotorComportamiento(object())

    def test_actualiza_la_configuracion_completa(self):
        motor = MotorComportamiento()
        nueva_configuracion = crear_configuracion(
            latencia_min=10,
            latencia_max=20,
            tasa_error=0.25,
        )

        self.assertTrue(motor.actualizarConfiguracion(nueva_configuracion))

        self.assertIs(motor.configuracionActual, nueva_configuracion)
        with self.assertRaises(TypeError):
            motor.actualizarConfiguracion(object())

    @patch("app.motor_comportamiento.time.sleep")
    @patch("app.motor_comportamiento.random.randint", return_value=75)
    def test_aplica_una_latencia_aleatoria_del_rango(
        self,
        randint_mock,
        sleep_mock,
    ):
        motor = MotorComportamiento()

        motor.aplicarEfectosDeRed()

        randint_mock.assert_called_once_with(50, 100)
        sleep_mock.assert_called_once_with(0.075)

    @patch("app.motor_comportamiento.time.sleep", side_effect=InterruptedError)
    @patch("app.motor_comportamiento.random.randint", return_value=50)
    def test_no_oculta_interrupciones_del_sleep(self, _randint_mock, _sleep_mock):
        with self.assertRaises(InterruptedError):
            MotorComportamiento().aplicarEfectosDeRed()

    def test_evalua_la_tasa_de_error_como_booleano(self):
        motor = MotorComportamiento(crear_configuracion(tasa_error=0.5))

        with patch("app.motor_comportamiento.random.random", return_value=0.49):
            self.assertTrue(motor.evaluarTasaError())
        with patch("app.motor_comportamiento.random.random", return_value=0.5):
            self.assertFalse(motor.evaluarTasaError())

    @patch("app.motor_comportamiento.Timer")
    def test_bloquea_cambios_y_repara_una_caida_temporal(self, timer_class_mock):
        motor = MotorComportamiento()
        configuracion_temporal = FabricaModosOpenFinance.modo_caida_temporal(15)
        temporizador = timer_class_mock.return_value

        self.assertTrue(motor.actualizarConfiguracion(configuracion_temporal))

        timer_class_mock.assert_called_once()
        segundos, reparar = timer_class_mock.call_args.args
        self.assertEqual(segundos, 15)
        self.assertTrue(temporizador.daemon)
        temporizador.start.assert_called_once_with()
        self.assertIs(motor.configuracionActual, configuracion_temporal)

        self.assertFalse(
            motor.actualizarConfiguracion(FabricaModosOpenFinance.modo_lento())
        )
        self.assertIs(motor.configuracionActual, configuracion_temporal)

        reparar()

        self.assertEqual(
            motor.configuracionActual,
            FabricaModosOpenFinance.modo_normal(),
        )
        self.assertTrue(
            motor.actualizarConfiguracion(FabricaModosOpenFinance.modo_lento())
        )

    def test_lecturas_concurrentes_no_observan_configuraciones_parciales(self):
        configuracion_normal = FabricaModosOpenFinance.modo_normal()
        configuracion_caida = FabricaModosOpenFinance.modo_caido()
        motor = MotorComportamiento(configuracion_normal)
        configuraciones_validas = {configuracion_normal, configuracion_caida}
        observaciones_invalidas = []

        def escribir():
            for indice in range(2000):
                motor.actualizarConfiguracion(
                    configuracion_normal if indice % 2 == 0 else configuracion_caida
                )

        def leer():
            for _ in range(2000):
                configuracion = motor.configuracionActual
                if configuracion not in configuraciones_validas:
                    observaciones_invalidas.append(configuracion)

        hilos = [Thread(target=escribir), Thread(target=leer), Thread(target=leer)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=2)

        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(observaciones_invalidas, [])

    def test_no_mantiene_el_lock_durante_el_sleep(self):
        motor = MotorComportamiento()
        inicio_sleep = Event()
        liberar_sleep = Event()
        actualizacion_terminada = Event()
        nueva_configuracion = FabricaModosOpenFinance.modo_lento()

        def sleep_bloqueado(_segundos):
            inicio_sleep.set()
            liberar_sleep.wait(timeout=2)

        def actualizar():
            motor.actualizarConfiguracion(nueva_configuracion)
            actualizacion_terminada.set()

        with patch(
            "app.motor_comportamiento.time.sleep",
            side_effect=sleep_bloqueado,
        ):
            hilo_sleep = Thread(target=motor.aplicarEfectosDeRed)
            hilo_actualizacion = Thread(target=actualizar)
            try:
                hilo_sleep.start()
                self.assertTrue(inicio_sleep.wait(timeout=1))
                hilo_actualizacion.start()
                self.assertTrue(actualizacion_terminada.wait(timeout=1))
            finally:
                liberar_sleep.set()
                hilo_sleep.join(timeout=2)
                hilo_actualizacion.join(timeout=2)


class MotorComportamientoEndpointTest(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self._limpiar_caida_temporal()
        motor_comportamiento.actualizarConfiguracion(
            FabricaModosOpenFinance.modo_normal()
        )

    def tearDown(self):
        self._limpiar_caida_temporal()
        motor_comportamiento.actualizarConfiguracion(
            FabricaModosOpenFinance.modo_normal()
        )

    @staticmethod
    def _limpiar_caida_temporal():
        temporizador = motor_comportamiento._temporizador_autoreparacion
        if temporizador is not None:
            temporizador.cancel()
            motor_comportamiento._repararAutomaticamente()

    def test_config_selecciona_y_devuelve_los_modos_permanentes(self):
        casos = (
            ("normal", FabricaModosOpenFinance.modo_normal()),
            ("caido", FabricaModosOpenFinance.modo_caido()),
            ("lento", FabricaModosOpenFinance.modo_lento()),
        )

        with app.test_client() as client:
            for modo, configuracion_esperada in casos:
                with self.subTest(modo=modo):
                    respuesta = client.post("/config", json={"modo": modo})

                    self.assertEqual(respuesta.status_code, 200)
                    self.assertEqual(
                        respuesta.get_json(),
                        serializar_configuracion(configuracion_esperada),
                    )
                    self.assertEqual(
                        motor_comportamiento.configuracionActual,
                        configuracion_esperada,
                    )

    def test_config_rechaza_cuerpos_invalidos(self):
        configuracion_inicial = motor_comportamiento.configuracionActual
        casos = (
            None,
            [],
            {},
            {"modo": "NORMAL"},
            {"modo": "desconocido"},
            {"modo": "normal", "autoRepararSegundos": 10},
            {"modo": "normal", "latenciaMinMs": 1},
            {"modo": "caido_temporal"},
            {"modo": "caido_temporal", "autoRepararSegundos": True},
            {"modo": "caido_temporal", "autoRepararSegundos": 1.5},
            {"modo": "caido_temporal", "autoRepararSegundos": 0},
            {"modo": "caido_temporal", "autoRepararSegundos": -1},
            {
                "modo": "caido_temporal",
                "autoRepararSegundos": 10,
                "tasaError": 0.5,
            },
        )

        with app.test_client() as client:
            for cuerpo in casos:
                with self.subTest(cuerpo=cuerpo):
                    respuesta = client.post("/config", json=cuerpo)
                    self.assertEqual(respuesta.status_code, 400)
                    self.assertEqual(
                        respuesta.get_json()["error"],
                        "CONFIGURACION_INVALIDA",
                    )

        self.assertIs(
            motor_comportamiento.configuracionActual,
            configuracion_inicial,
        )

    @patch("app.motor_comportamiento.Timer")
    def test_config_bloquea_cambios_hasta_la_autoreparacion(
        self,
        timer_class_mock,
    ):
        temporizador = timer_class_mock.return_value

        with app.test_client() as client:
            respuesta_temporal = client.post(
                "/config",
                json={"modo": "caido_temporal", "autoRepararSegundos": 10},
            )
            respuesta_bloqueada = client.post("/config", json={"modo": "normal"})

            _, reparar = timer_class_mock.call_args.args
            reparar()

            respuesta_posterior = client.post("/config", json={"modo": "lento"})

        self.assertEqual(respuesta_temporal.status_code, 200)
        self.assertEqual(
            respuesta_temporal.get_json(),
            serializar_configuracion(
                FabricaModosOpenFinance.modo_caida_temporal(10)
            ),
        )
        self.assertTrue(temporizador.daemon)
        temporizador.start.assert_called_once_with()
        self.assertEqual(respuesta_bloqueada.status_code, 409)
        self.assertEqual(
            respuesta_bloqueada.get_json(),
            {"error": "MODO_TEMPORAL_ACTIVO"},
        )
        self.assertEqual(respuesta_posterior.status_code, 200)
        self.assertEqual(
            motor_comportamiento.configuracionActual,
            FabricaModosOpenFinance.modo_lento(),
        )

    def test_health_expone_la_configuracion_sin_aplicar_efectos(self):
        configuracion = FabricaModosOpenFinance.modo_caido()
        motor_comportamiento.actualizarConfiguracion(configuracion)

        with patch.object(motor_comportamiento, "aplicarEfectosDeRed") as aplicar:
            with patch.object(motor_comportamiento, "evaluarTasaError") as evaluar:
                with app.test_client() as client:
                    respuesta = client.get("/health")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta.get_json(),
            {
                "status": "ok",
                "service": "mock-openfinance",
                "configuracion": serializar_configuracion(configuracion),
            },
        )
        aplicar.assert_not_called()
        evaluar.assert_not_called()

    def test_perfil_aplica_latencia_antes_de_evaluar_la_falla(self):
        orden = []

        with patch.object(
            motor_comportamiento,
            "aplicarEfectosDeRed",
            side_effect=lambda: orden.append("latencia"),
        ):
            with patch.object(
                motor_comportamiento,
                "evaluarTasaError",
                side_effect=lambda: orden.append("falla") or False,
            ):
                with app.test_client() as client:
                    respuesta = client.get("/perfil/cliente-123")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(orden, ["latencia", "falla"])

    def test_perfil_responde_503_en_modo_caido(self):
        motor_comportamiento.actualizarConfiguracion(
            FabricaModosOpenFinance.modo_caido()
        )

        with patch.object(motor_comportamiento, "aplicarEfectosDeRed"):
            with patch("app.motor_comportamiento.random.random", return_value=0.0):
                with app.test_client() as client:
                    respuesta = client.get("/perfil/cliente-123")

        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.get_json(), {"error": "FALLA_SIMULADA"})


if __name__ == "__main__":
    unittest.main()
