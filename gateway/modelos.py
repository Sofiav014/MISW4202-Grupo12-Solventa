from dataclasses import dataclass
from datetime import datetime
from typing import Union


@dataclass(frozen=True)
class SolicitudProtegida:
    """
    Representa una solicitud HTTP que debe ser procesada por la pasarela.

    Contiene toda la información necesaria para validar la solicitud,
    evaluar su contexto de seguridad y, si corresponde, reenviarla al
    servicio de destino.

    Attributes:
        metodo:
            Método HTTP de la solicitud, por ejemplo GET, POST o PUT.

        ruta:
            Ruta solicitada, sin incluir la cadena de consulta.

        cadena_consulta:
            Cadena de parámetros de consulta de la solicitud.

        cabeceras:
            Cabeceras HTTP recibidas. Incluye información utilizada por
            componentes de seguridad como Authorization y X-Session-Context.

        cuerpo:
            Cuerpo original de la solicitud representado como bytes.

        id_solicitud:
            Identificador interno único utilizado para trazabilidad de la
            solicitud a través de la pasarela.

        id_correlacion_externo:
            Identificador de correlación proporcionado por un sistema externo,
            si existe. Permite relacionar la solicitud con trazas externas.
    """

    metodo: str
    ruta: str
    cadena_consulta: str
    cabeceras: dict[str, str]
    cuerpo: bytes
    id_solicitud: str
    id_correlacion_externo: str | None


@dataclass(frozen=True)
class ContextoSesion:
    """
    Representa el contexto de una sesión previamente validado.

    Este modelo agrupa la información de sesión, dispositivo, ubicación y
    tiempo utilizada por los mecanismos de detección y evaluación de riesgo.

    Attributes:
        id_sesion:
            Identificador de la sesión asociada a la solicitud.

        id_dispositivo:
            Identificador del dispositivo desde el cual se realiza la solicitud.

        latitud:
            Latitud geográfica reportada por la sesión.

        longitud:
            Longitud geográfica reportada por la sesión.

        instante:
            Fecha y hora asociadas al contexto de sesión.
    """

    id_sesion: str
    id_dispositivo: str
    latitud: float
    longitud: float
    instante: datetime


@dataclass(frozen=True)
class FalloValidacion:
    """
    Representa un error producido durante la validación de una solicitud.

    Se utiliza para comunicar errores de validación entre componentes sin
    acoplar las capas superiores a excepciones o detalles de implementación.

    Attributes:
        codigo:
            Código estable que identifica el tipo de fallo.

        detalle_interno:
            Información técnica adicional sobre el error. Está orientada a
            diagnóstico y trazabilidad, no necesariamente al cliente final.
    """

    codigo: str
    detalle_interno: str = ""


@dataclass(frozen=True)
class ErrorPasarela:
    """
    Representa una respuesta de error generada directamente por la pasarela.

    Se utiliza cuando la solicitud no debe continuar hacia el servicio de
    destino, por ejemplo debido a autenticación inválida, contexto incorrecto
    o una decisión de seguridad.

    Attributes:
        codigo_error:
            Código estable y procesable que identifica el error.

        estado_http:
            Código de estado HTTP que debe devolver la pasarela.

        mensaje:
            Mensaje que puede incluirse en la respuesta al consumidor.

        id_solicitud:
            Identificador de la solicitud asociada al error para facilitar
            trazabilidad y diagnóstico.
    """

    codigo_error: str
    estado_http: int
    mensaje: str
    id_solicitud: str


@dataclass(frozen=True)
class VeredictoDetector:
    """
    Representa la decisión producida por el servicio Detector.

    El Detector evalúa el contexto de una sesión y determina el resultado
    de la evaluación de riesgo.

    Attributes:
        veredicto:
            Decisión producida por el Detector.

        motivo:
            Explicación o código asociado al motivo del veredicto, cuando
            el Detector proporciona uno.

        score_riesgo:
            Puntaje numérico calculado por el Detector para representar
            el nivel de riesgo de la solicitud.
    """

    veredicto: str
    motivo: str | None
    score_riesgo: float


@dataclass(frozen=True)
class RespuestaJourney:
    """
    Representa una respuesta HTTP obtenida del servicio Journey.

    La pasarela puede devolver esta respuesta al consumidor cuando la
    solicitud supera las validaciones y políticas correspondientes.

    Attributes:
        estado:
            Código de estado HTTP devuelto por Journey.

        cuerpo:
            Cuerpo de la respuesta en su representación original como bytes.

        cabeceras:
            Cabeceras HTTP devueltas por Journey.
    """

    estado: int
    cuerpo: bytes
    cabeceras: dict[str, str]


@dataclass(frozen=True)
class FalloDependencia:
    """
    Describe un fallo ocurrido al comunicarse con una dependencia externa.

    Permite representar de manera uniforme fallos de servicios como Detector,
    Journey o Identidad, incluyendo información útil para observabilidad.

    Attributes:
        dependencia:
            Nombre de la dependencia que presentó el fallo.

        categoria:
            Clasificación del fallo, por ejemplo timeout, conexión o respuesta
            inválida.

        latencia_ms:
            Tiempo transcurrido en la operación antes de detectar el fallo,
            expresado en milisegundos.

        causa:
            Detalle técnico adicional sobre la causa del fallo, cuando existe.
    """

    dependencia: str
    categoria: str
    latencia_ms: float
    causa: str | None = None


@dataclass(frozen=True)
class ResultadoDetector:
    """
    Representa el resultado de una llamada al servicio Detector.

    Una ejecución exitosa puede contener un ``VeredictoDetector``. Si la
    comunicación o procesamiento de la dependencia falla, el resultado puede
    contener un ``FalloDependencia``.

    Attributes:
        veredicto:
            Veredicto obtenido del Detector cuando la operación fue exitosa.

        fallo:
            Información del fallo cuando no fue posible obtener un veredicto.

        latencia_ms:
            Latencia total observada en la llamada al Detector, cuando está
            disponible.
    """

    veredicto: VeredictoDetector | None = None
    fallo: FalloDependencia | None = None
    latencia_ms: float | None = None


@dataclass(frozen=True)
class ResultadoJourney:
    """
    Representa el resultado de una llamada al servicio Journey.

    Puede contener la respuesta HTTP recibida de Journey o la descripción
    de un fallo ocurrido al intentar comunicarse con esa dependencia.

    Attributes:
        respuesta:
            Respuesta obtenida de Journey cuando la operación fue exitosa.

        fallo:
            Información sobre el fallo de dependencia cuando Journey no pudo
            responder correctamente.
    """

    respuesta: RespuestaJourney | None = None
    fallo: FalloDependencia | None = None


@dataclass(frozen=True)
class ConfirmacionIdentidad:
    """
    Representa la confirmación devuelta por el servicio de Identidad.

    Se utiliza para registrar el resultado de iniciar o gestionar un proceso
    de verificación de identidad reforzada.

    Attributes:
        estado:
            Estado reportado por el servicio de Identidad.

        referencia:
            Identificador o referencia del proceso de verificación generado
            por el servicio.
    """

    estado: str
    referencia: str


@dataclass(frozen=True)
class ResultadoIdentidad:
    """
    Representa el resultado de una llamada al servicio de Identidad.

    Puede contener una confirmación válida del proceso de verificación o
    información sobre un fallo ocurrido durante la comunicación.

    Attributes:
        confirmacion:
            Confirmación retornada por Identidad cuando la operación fue
            procesada correctamente.

        fallo:
            Información del fallo cuando no fue posible obtener la
            confirmación.
    """

    confirmacion: ConfirmacionIdentidad | None = None
    fallo: FalloDependencia | None = None


# Tipo de resultado que puede producir la pasarela después de procesar una
# solicitud: un error generado por la propia pasarela o la respuesta obtenida
# del servicio Journey.
ResultadoPasarela = Union[ErrorPasarela, RespuestaJourney]