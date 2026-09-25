import uuid

from flask import Flask, request, g, jsonify

from .configuracion import ConfiguracionPasarela
from .modelos import SolicitudProtegida
from .politica import PolicyGate
from .respuestas import AdaptadorRespuesta
from .validacion import AnalizadorContextoSesion, ValidadorPyJwt
from .clientes import ClienteDetector, ClienteHttpJourney, ClienteIdentidad, TransporteHttp
from .auditoria import RegistradorAuditoriaJson


# Métodos HTTP que pueden ser procesados por las rutas protegidas
# expuestas por la pasarela.
METODOS_PROTEGIDOS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def crear_aplicacion(configuracion: ConfiguracionPasarela, *, orquestador=None) -> Flask:
    """
    Crea y configura la aplicación Flask que funciona como pasarela.

    La función construye las dependencias necesarias para procesar las
    solicitudes dirigidas a Journey:

    - validación del JWT;
    - análisis del contexto de sesión;
    - comunicación con Detector;
    - comunicación con Journey;
    - comunicación con Identidad;
    - registro de auditoría;
    - adaptación del resultado interno a una respuesta HTTP.

    También registra la ruta protegida de Journey y un manejador global
    para excepciones inesperadas.

    Args:
        configuracion:
            Configuración utilizada para construir la pasarela y sus clientes,
            incluyendo URLs, tiempos de espera, parámetros JWT y opciones
            relacionadas con escenarios de prueba.

        orquestador:
            Orquestador alternativo que puede sustituir al ``PolicyGate``
            construido por defecto. Resulta útil, por ejemplo, para pruebas
            donde se desea inyectar una implementación controlada.

    Returns:
        Aplicación Flask completamente configurada.
    """
    aplicacion = Flask(__name__)
    auditoria = RegistradorAuditoriaJson()

    # Si no se proporciona un orquestador externo, se construye el PolicyGate
    # con todas las dependencias necesarias para ejecutar el flujo completo:
    # autenticación -> contexto -> Detector -> Identidad/Journey.
    puerta = orquestador or PolicyGate(ValidadorPyJwt(configuracion.secreto_jwt, configuracion.algoritmo_jwt), AnalizadorContextoSesion(),
        ClienteDetector(TransporteHttp(), configuracion.url_detector, configuracion.tiempo_espera_detector_ms, permitir_escenario_stub=configuracion.permitir_escenario_stub),
        ClienteHttpJourney(TransporteHttp(), configuracion.url_journey, configuracion.tiempo_espera_journey_ms, permitir_escenario_stub=configuracion.permitir_escenario_stub), ClienteIdentidad(TransporteHttp(), configuracion.url_identidad, configuracion.tiempo_espera_identidad_ms, permitir_escenario_stub=configuracion.permitir_escenario_stub), auditoria=auditoria)

    # Traduce los modelos internos producidos por PolicyGate a respuestas
    # compatibles con Flask/HTTP.
    adaptador = AdaptadorRespuesta()

    @aplicacion.route(f"{configuracion.prefijo_journey}/<path:ruta>", methods=METODOS_PROTEGIDOS)
    def manejar_ruta_journey(ruta: str):
        """
        Procesa una solicitud HTTP dirigida a una ruta protegida de Journey.

        Para cada solicitud:

        1. Genera un identificador interno único.
        2. Conserva dicho identificador en el contexto Flask para que pueda
           utilizarse también durante el manejo de errores.
        3. Convierte la solicitud Flask en un ``SolicitudProtegida``.
        4. Delega la decisión y ejecución del flujo al ``PolicyGate``.
        5. Convierte el resultado de dominio a una respuesta HTTP.

        Args:
            ruta:
                Fragmento de ruta capturado después del prefijo configurado
                para Journey.

        Returns:
            Respuesta HTTP producida a partir del resultado del orquestador.
        """
        # Cada petición recibe un identificador propio independientemente de
        # cualquier identificador enviado por el consumidor.
        id_solicitud = str(uuid.uuid4())

        # Se guarda en ``g`` para que el mismo request_id pueda recuperarse
        # posteriormente desde el manejador global de excepciones.
        g.id_solicitud = id_solicitud

        # Se crea una representación independiente de Flask con todos los
        # datos que necesita la capa de política para procesar la solicitud.
        solicitud = SolicitudProtegida(
            request.method, ruta, request.query_string.decode(), dict(request.headers),
            request.get_data(), id_solicitud, request.headers.get("X-Request-ID"),
        )

        # PolicyGate devuelve modelos de dominio; AdaptadorRespuesta se
        # encarga de convertirlos al formato esperado por Flask.
        return adaptador.a_http(puerta.manejar(solicitud))

    @aplicacion.errorhandler(Exception)
    def manejar_excepcion(error):
        """
        Maneja cualquier excepción no controlada producida por la pasarela.

        El error se registra utilizando el identificador de la solicitud
        actual. Si la excepción ocurre antes de que dicho identificador haya
        sido creado, se genera uno nuevo.

        La respuesta enviada al consumidor es deliberadamente genérica para
        no exponer detalles internos de la excepción.

        Args:
            error:
                Excepción inesperada capturada por Flask.

        Returns:
            Respuesta JSON con estado HTTP 500 y un identificador de solicitud
            que permite correlacionar el error con los registros internos.
        """
        # Normalmente el identificador ya fue creado al entrar en la ruta.
        # El valor alternativo cubre errores ocurridos antes de ese punto.
        id_solicitud = getattr(g, "id_solicitud", str(uuid.uuid4()))

        # El detalle completo de la excepción queda en los logs, mientras que
        # al consumidor solo se le devuelve un error estable y genérico.
        aplicacion.logger.exception("unexpected gateway exception", extra={"request_id": id_solicitud})

        return jsonify(error_code="gateway_internal_error", message="The gateway could not complete the request.", request_id=id_solicitud), 500

    return aplicacion