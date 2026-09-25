import json
import math

from ..modelos import FalloDependencia, ResultadoDetector, VeredictoDetector
from .transporte import FalloTransporte


class ClienteDetector:
    """
    Cliente encargado de comunicarse con el servicio Detector.

    El Detector recibe el contexto de una sesión y devuelve un veredicto
    de riesgo. Este cliente encapsula:

    - construcción de la solicitud HTTP;
    - propagación opcional de escenarios stub;
    - manejo de fallos de transporte;
    - validación del presupuesto de latencia;
    - validación estricta del contrato de respuesta;
    - conversión de la respuesta externa a modelos internos del sistema.
    """

    def __init__(self, transporte, url, tiempo_espera_ms=200, presupuesto_ms=200, permitir_escenario_stub=False):
        """
        Inicializa el cliente del servicio Detector.

        Args:
            transporte:
                Componente encargado de ejecutar las solicitudes HTTP.

            url:
                URL base del servicio Detector. Se elimina cualquier "/"
                final para construir las rutas de forma consistente.

            tiempo_espera_ms:
                Tiempo máximo, en milisegundos, que el transporte debe esperar
                por la respuesta del Detector.

            presupuesto_ms:
                Latencia máxima aceptable para considerar válida una respuesta
                del Detector, incluso si técnicamente fue recibida a tiempo.

            permitir_escenario_stub:
                Indica si se permite enviar la cabecera X-Escenario-Stub,
                utilizada para controlar escenarios simulados de prueba.
        """
        self.transporte, self.url, self.tiempo_espera_ms, self.presupuesto_ms, self.permitir_escenario_stub = transporte, url.rstrip("/"), tiempo_espera_ms, presupuesto_ms, permitir_escenario_stub

    def evaluar(self, contexto, id_solicitud, escenario=None):
        """
        Solicita al Detector una evaluación de riesgo para una sesión.

        El contexto de sesión se serializa a JSON y se envía mediante POST
        al endpoint ``/evaluar``.

        El resultado puede representar:

        - un veredicto válido del Detector;
        - un fallo de transporte;
        - una respuesta que excedió el presupuesto de latencia;
        - una respuesta que no cumple el contrato esperado.

        Args:
            contexto:
                Contexto de sesión previamente validado. Debe proporcionar
                identificadores de sesión y dispositivo, coordenadas e instante.

            id_solicitud:
                Identificador interno de la solicitud, utilizado para mantener
                trazabilidad entre la pasarela y el Detector.

            escenario:
                Nombre opcional de un escenario stub. Solo se propaga cuando
                ``permitir_escenario_stub`` está habilitado.

        Returns:
            ``ResultadoDetector`` con un ``VeredictoDetector`` cuando la
            respuesta es válida, o con un ``FalloDependencia`` cuando ocurre
            algún error de comunicación, latencia o contrato.
        """
        # Construye el contrato que el Detector espera recibir. Los nombres de
        # los campos externos se mantienen independientes de los nombres usados
        # por los modelos internos de la pasarela.
        datos = {
            "session_id": contexto.id_sesion,
            "device_id": contexto.id_dispositivo,
            "geo_lat": contexto.latitud,
            "geo_lon": contexto.longitud,
            "timestamp": contexto.instante.isoformat(),
            "request_id": id_solicitud
        }

        cabeceras = {"Content-Type": "application/json"}

        # La cabecera de escenario se utiliza únicamente en entornos donde
        # explícitamente se permite controlar el comportamiento del stub.
        if escenario and self.permitir_escenario_stub:
            cabeceras["X-Escenario-Stub"] = escenario

        r = self.transporte.solicitar(
            "POST",
            self.url + "/evaluar",
            cabeceras=cabeceras,
            cuerpo=json.dumps(datos).encode(),
            tiempo_espera_ms=self.tiempo_espera_ms
        )

        # Los fallos de red o transporte se traducen al modelo uniforme de
        # fallos de dependencia utilizado por la pasarela.
        if isinstance(r, FalloTransporte):
            return ResultadoDetector(
                fallo=FalloDependencia(
                    "detector",
                    r.categoria,
                    r.latencia_ms,
                    r.categoria
                )
            )

        # Una respuesta puede haber llegado correctamente a nivel HTTP pero
        # ser demasiado lenta para cumplir el presupuesto operativo definido.
        if r.latencia_ms > self.presupuesto_ms:
            return ResultadoDetector(
                fallo=FalloDependencia(
                    "detector",
                    "presupuesto_excedido",
                    r.latencia_ms,
                    "latency_budget"
                )
            )

        try:
            d = json.loads(r.cuerpo)

            # La respuesta del Detector se valida de forma estricta:
            #
            # - debe ser un objeto JSON;
            # - debe responder HTTP 200;
            # - debe contener exactamente los campos esperados;
            # - el veredicto debe pertenecer al conjunto permitido;
            # - el motivo debe ser uno de los valores conocidos o None;
            # - el score debe ser numérico, pero no booleano;
            # - el score debe ser finito, evitando NaN e infinitos.
            if (not isinstance(d, dict)
                    or r.estado != 200 or set(d) != {"veredicto", "motivo", "score_riesgo"}
                    or d.get("veredicto") not in {"normal", "sospechoso"}
                    or d.get("motivo") not in {None, "dispositivo", "ubicacion"}
                    or isinstance(d.get("score_riesgo"), bool)
                    or not isinstance(d.get("score_riesgo"), (int, float))
                    or not math.isfinite(d["score_riesgo"])):
                raise ValueError

            # Una respuesta válida se transforma a los modelos internos para
            # que el resto de la aplicación no dependa del JSON externo.
            return ResultadoDetector(
                veredicto=VeredictoDetector(
                    d["veredicto"],
                    d.get("motivo"),
                    float(d["score_riesgo"])
                ),
                latencia_ms=r.latencia_ms
            )

        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError):
            # Diferencia entre un estado HTTP inesperado y un cuerpo que no
            # satisface el contrato, aunque ambos se normalizan como un fallo
            # de contrato del Detector.
            causa = "http_status" if r.estado != 200 else "invalid_contract"

            return ResultadoDetector(
                fallo=FalloDependencia(
                    "detector",
                    "contrato_invalido",
                    r.latencia_ms,
                    causa
                )
            )