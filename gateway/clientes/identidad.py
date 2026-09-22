import json

from ..modelos import ConfirmacionIdentidad, FalloDependencia, ResultadoIdentidad
from .transporte import FalloTransporte


class ClienteIdentidad:
    """
    Cliente encargado de comunicarse con el servicio de Identidad.

    Su responsabilidad es iniciar un proceso de verificación reforzada para
    una sesión determinada y transformar la respuesta externa del servicio
    en modelos internos de la pasarela.

    También normaliza los fallos de transporte y las respuestas que no cumplen
    el contrato esperado.
    """

    def __init__(self, transporte, url, tiempo_espera_ms=500, permitir_escenario_stub=False):
        """
        Inicializa el cliente del servicio de Identidad.

        Args:
            transporte:
                Componente encargado de realizar las solicitudes HTTP.

            url:
                URL base del servicio de Identidad. Se elimina cualquier "/"
                final para construir las rutas de forma consistente.

            tiempo_espera_ms:
                Tiempo máximo, en milisegundos, que se esperará por una
                respuesta del servicio.

            permitir_escenario_stub:
                Indica si puede enviarse la cabecera X-Escenario-Stub para
                controlar escenarios simulados durante pruebas.
        """
        self.transporte, self.url, self.tiempo_espera_ms, self.permitir_escenario_stub = transporte, url.rstrip("/"), tiempo_espera_ms, permitir_escenario_stub

    def iniciar_verificacion(self, id_sesion, id_solicitud, escenario=None):
        """
        Solicita al servicio de Identidad el inicio de una verificación reforzada.

        Envía el identificador de la sesión y el identificador interno de la
        solicitud al endpoint ``/verificacion/iniciar``.

        La operación puede producir:

        - una confirmación de que la verificación fue iniciada o quedó pendiente;
        - un fallo de transporte;
        - un fallo porque la respuesta no cumple el contrato esperado.

        Args:
            id_sesion:
                Identificador de la sesión que debe someterse al proceso de
                verificación.

            id_solicitud:
                Identificador interno de la solicitud utilizado para mantener
                trazabilidad entre la pasarela y el servicio de Identidad.

            escenario:
                Escenario stub opcional. Solo se propaga cuando
                ``permitir_escenario_stub`` está habilitado.

        Returns:
            ``ResultadoIdentidad`` con una ``ConfirmacionIdentidad`` cuando la
            operación es válida, o con un ``FalloDependencia`` cuando ocurre
            un error de transporte o de contrato.
        """
        cabeceras = {"Content-Type": "application/json"}

        # Los escenarios simulados solo se propagan cuando esta posibilidad
        # fue habilitada explícitamente en la configuración del cliente.
        if escenario and self.permitir_escenario_stub:
            cabeceras["X-Escenario-Stub"] = escenario

        # El servicio de Identidad necesita tanto la sesión que debe verificarse
        # como el request_id utilizado para correlacionar la operación.
        cuerpo = json.dumps({
            "session_id": id_sesion,
            "request_id": id_solicitud
        }).encode()

        respuesta = self.transporte.solicitar(
            "POST",
            self.url + "/verificacion/iniciar",
            cabeceras=cabeceras,
            cuerpo=cuerpo,
            tiempo_espera_ms=self.tiempo_espera_ms
        )

        # Los errores producidos por la capa de transporte se convierten al
        # modelo común de fallos de dependencia utilizado por la pasarela.
        if isinstance(respuesta, FalloTransporte):
            return ResultadoIdentidad(
                fallo=FalloDependencia(
                    "identidad",
                    respuesta.categoria,
                    respuesta.latencia_ms,
                    respuesta.categoria
                )
            )

        try:
            datos = json.loads(respuesta.cuerpo)

            # La respuesta se valida de manera estricta:
            #
            # - debe responder con HTTP 200;
            # - debe contener exactamente "status" y "reference";
            # - el estado debe representar una verificación iniciada o pendiente;
            # - la referencia debe ser un texto no vacío.
            if (respuesta.estado != 200
                    or set(datos) != {"status", "reference"}
                    or datos["status"] not in {"verification_started", "pending"}
                    or not isinstance(datos["reference"], str)
                    or not datos["reference"].strip()):
                raise ValueError

            # Una respuesta válida se transforma al modelo interno para evitar
            # que el resto del sistema dependa directamente del JSON externo.
            return ResultadoIdentidad(
                ConfirmacionIdentidad(
                    datos["status"],
                    datos["reference"]
                )
            )

        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError):
            # Se conserva la diferencia entre un código HTTP inesperado y una
            # respuesta cuyo contenido no satisface el contrato esperado.
            causa = "http_status" if respuesta.estado != 200 else "invalid_contract"

            return ResultadoIdentidad(
                fallo=FalloDependencia(
                    "identidad",
                    "contrato_invalido",
                    respuesta.latencia_ms,
                    causa
                )
            )