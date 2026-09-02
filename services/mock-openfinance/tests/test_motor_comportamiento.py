import unittest
from dataclasses import FrozenInstanceError
from threading import Event, Thread
from unittest.mock import patch

from app.main import app, motor_comportamiento
from app.motor_comportamiento import (
    ConfiguracionComportamientoDTO,
    FallaSimuladaException,
    MotorComportamiento,
    TipoFallaEnum,
)


def crear_configuracion(
    latencia_min=0,
    latencia_max=0,
    tasa_error=0.0,
    tipo_falla=TipoFallaEnum.NINGUNA,
    codigo_http=200,
):
    return ConfiguracionComportamientoDTO(
        latenciaMinMs=latencia_min,
        latenciaMaxMs=latencia_max,
        tasaError=tasa_error,
        tipoFalla=tipo_falla,
        codigoHttpRespuesta=codigo_http,
    )


def serializar_configuracion(configuracion):
    return {
        "latenciaMinMs": configuracion.latenciaMinMs,
        "latenciaMaxMs": configuracion.latenciaMaxMs,
        "tasaError": configuracion.tasaError,
        "tipoFalla": configuracion.tipoFalla.value,
        "codigoHttpRespuesta": configuracion.codigoHttpRespuesta,
    }


class ConfiguracionComportamientoDTOTest(unittest.TestCase):
    def test_es_inmutable(self):
        configuracion = crear_configuracion()

        with self.assertRaises(FrozenInstanceError):
            configuracion.tasaError = 0.5

    def test_rechaza_configuraciones_invalidas(self):
        configuracion_base = {
            "latenciaMinMs": 0,
            "latenciaMaxMs": 100,
            "tasaError": 0.0,
            "tipoFalla": TipoFallaEnum.NINGUNA,
            "codigoHttpRespuesta": 200,
        }
        casos = (
            ({"latenciaMinMs": True}, TypeError),
            ({"latenciaMaxMs": 1.5}, TypeError),
            ({"latenciaMinMs": -1}, ValueError),
            ({"latenciaMinMs": 101}, ValueError),
            ({"tasaError": "0.5"}, TypeError),
            ({"tasaError": -0.1}, ValueError),
            ({"tasaError": 1.1}, ValueError),
            ({"tipoFalla": "NINGUNA"}, TypeError),
            ({"codigoHttpRespuesta": True}, TypeError),
            ({"codigoHttpRespuesta": 99}, ValueError),
            (
                {
                    "tipoFalla": TipoFallaEnum.HTTP_ERROR,
                    "codigoHttpRespuesta": 200,
                },
                ValueError,
            ),
            ({"tasaError": 0.1}, ValueError),
        )

        for cambios, error_esperado in casos:
            with self.subTest(cambios=cambios):
                datos = configuracion_base | cambios
                with self.assertRaises(error_esperado):
                    ConfiguracionComportamientoDTO(**datos)


class MotorComportamientoTest(unittest.TestCase):
    def test_inicia_con_configuracion_sana(self):
        configuracion = MotorComportamiento().configuracionActual

        self.assertEqual(configuracion.latenciaMinMs, 50)
        self.assertEqual(configuracion.latenciaMaxMs, 100)
        self.assertEqual(configuracion.tasaError, 0.0)
        self.assertIs(configuracion.tipoFalla, TipoFallaEnum.NINGUNA)
        self.assertEqual(configuracion.codigoHttpRespuesta, 200)

    def test_actualiza_la_configuracion_completa(self):
        motor = MotorComportamiento()
        nueva_configuracion = crear_configuracion(
            latencia_min=10,
            latencia_max=20,
            tasa_error=0.25,
            tipo_falla=TipoFallaEnum.HTTP_ERROR,
            codigo_http=429,
        )

        motor.actualizarConfiguracion(nueva_configuracion)

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

    def test_lanza_falla_solo_si_el_aleatorio_es_menor_que_la_tasa(self):
        motor = MotorComportamiento(
            crear_configuracion(
                tasa_error=0.5,
                tipo_falla=TipoFallaEnum.HTTP_ERROR,
                codigo_http=503,
            )
        )

        with patch("app.motor_comportamiento.random.random", return_value=0.49):
            with self.assertRaises(FallaSimuladaException) as contexto:
                motor.evaluarTasaError()

        self.assertIs(contexto.exception.tipoFalla, TipoFallaEnum.HTTP_ERROR)
        self.assertEqual(contexto.exception.codigoHttpRespuesta, 503)

        with patch("app.motor_comportamiento.random.random", return_value=0.5):
            motor.evaluarTasaError()

    def test_lecturas_concurrentes_no_observan_configuraciones_parciales(self):
        configuracion_sana = crear_configuracion()
        configuracion_fallida = crear_configuracion(
            latencia_min=300,
            latencia_max=400,
            tasa_error=1.0,
            tipo_falla=TipoFallaEnum.HTTP_ERROR,
            codigo_http=500,
        )
        motor = MotorComportamiento(configuracion_sana)
        configuraciones_validas = {configuracion_sana, configuracion_fallida}
        observaciones_invalidas = []

        def escribir():
            for indice in range(2000):
                motor.actualizarConfiguracion(
                    configuracion_sana if indice % 2 == 0 else configuracion_fallida
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
        nueva_configuracion = crear_configuracion()

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
        motor_comportamiento.actualizarConfiguracion(crear_configuracion())

    def tearDown(self):
        motor_comportamiento.actualizarConfiguracion(crear_configuracion())

    def test_config_reemplaza_y_devuelve_la_configuracion(self):
        datos = {
            "latenciaMinMs": 10,
            "latenciaMaxMs": 20,
            "tasaError": 0.25,
            "tipoFalla": "HTTP_ERROR",
            "codigoHttpRespuesta": 429,
        }

        with app.test_client() as client:
            respuesta = client.post("/config", json=datos)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.get_json(), datos)
        self.assertEqual(
            serializar_configuracion(motor_comportamiento.configuracionActual),
            datos,
        )

    def test_config_rechaza_cuerpos_incompletos_o_con_campos_adicionales(self):
        configuracion_inicial = motor_comportamiento.configuracionActual
        cuerpo_incompleto = serializar_configuracion(configuracion_inicial)
        cuerpo_incompleto.pop("tasaError")
        cuerpo_adicional = serializar_configuracion(configuracion_inicial)
        cuerpo_adicional["modo"] = "DOWN"

        with app.test_client() as client:
            for cuerpo in (cuerpo_incompleto, cuerpo_adicional):
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

    def test_health_expone_la_configuracion_sin_aplicar_efectos(self):
        configuracion = crear_configuracion(
            tasa_error=1.0,
            tipo_falla=TipoFallaEnum.HTTP_ERROR,
            codigo_http=500,
        )
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
                side_effect=lambda: orden.append("falla"),
            ):
                with app.test_client() as client:
                    respuesta = client.get("/perfil/cliente-123")

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(orden, ["latencia", "falla"])

    def test_perfil_traduce_los_tipos_de_falla(self):
        casos = (
            (TipoFallaEnum.HTTP_ERROR, 429, 429),
            (TipoFallaEnum.CONNECTION_REFUSED, 200, 503),
            (TipoFallaEnum.DROP_CONNECTION, 200, 503),
        )

        with app.test_client() as client:
            for tipo_falla, codigo_configurado, codigo_esperado in casos:
                with self.subTest(tipo_falla=tipo_falla):
                    motor_comportamiento.actualizarConfiguracion(
                        crear_configuracion(
                            tasa_error=1.0,
                            tipo_falla=tipo_falla,
                            codigo_http=codigo_configurado,
                        )
                    )
                    with patch.object(
                        motor_comportamiento,
                        "aplicarEfectosDeRed",
                    ):
                        with patch(
                            "app.motor_comportamiento.random.random",
                            return_value=0.0,
                        ):
                            respuesta = client.get("/perfil/cliente-123")

                    self.assertEqual(respuesta.status_code, codigo_esperado)
                    self.assertEqual(
                        respuesta.get_json(),
                        {
                            "error": "FALLA_SIMULADA",
                            "tipoFalla": tipo_falla.value,
                            "codigoHttpRespuesta": codigo_esperado,
                        },
                    )


if __name__ == "__main__":
    unittest.main()
