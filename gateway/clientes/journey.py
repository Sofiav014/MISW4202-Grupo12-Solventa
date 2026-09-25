from ..modelos import FalloDependencia, RespuestaJourney, ResultadoJourney
from .transporte import FalloTransporte


# Cabeceras de la solicitud original que pueden reenviarse al servicio Journey.
# El resto de cabeceras recibidas por la pasarela se descartan.
CABECERAS_JOURNEY = {
    "Authorization",
    "Content-Type",
    "Accept",
    "Accept-Language"
}

# Cabeceras de la respuesta de Journey que la pasarela permite propagar
# nuevamente hacia el consumidor.
CABECERAS_RESPUESTA = {
    "Content-Type",
    "Content-Length",
    "Location",
    "Cache-Control",
    "ETag"
}


class ClienteHttpJourney:
    """
    Cliente HTTP encargado de reenviar solicitudes autorizadas hacia Journey.

    Este cliente actúa como intermediario entre la pasarela y Journey. Su
    responsabilidad es:

    - filtrar las cabeceras que pueden enviarse al servicio;
    - añadir el identificador interno de la solicitud;
    - propagar escenarios stub de Journey cuando estén habilitados;
    - reconstruir la URL con su ruta y cadena de consulta;
    - ejecutar la solicitud mediante el transporte configurado;
    - convertir fallos de transporte al modelo común de dependencias;
    - filtrar las cabeceras de la respuesta antes de devolverla.
    """

    def __init__(self, transporte, url, tiempo_espera_ms=500, permitir_escenario_stub=False):
        """
        Inicializa el cliente HTTP de Journey.

        Args:
            transporte:
                Componente responsable de ejecutar las solicitudes HTTP.

            url:
                URL base del servicio Journey. Se elimina cualquier "/"
                final para evitar duplicados al construir las rutas.

            tiempo_espera_ms:
                Tiempo máximo, en milisegundos, que se esperará por una
                respuesta del servicio Journey.

            permitir_escenario_stub:
                Indica si se permite propagar escenarios simulados de Journey
                mediante la cabecera X-Escenario-Stub.
        """
        self.transporte, self.url, self.tiempo_espera_ms, self.permitir_escenario_stub = transporte, url.rstrip("/"), tiempo_espera_ms, permitir_escenario_stub

    def reenviar(self, solicitud):
        """
        Reenvía una solicitud protegida hacia el servicio Journey.

        Antes de realizar la llamada se filtran las cabeceras de entrada para
        evitar propagar información que Journey no necesita. También se añade
        el identificador interno de la solicitud para mantener la trazabilidad.

        Si la llamada falla a nivel de transporte, el error se convierte en
        un ``FalloDependencia``. Cuando Journey responde correctamente a nivel
        de transporte, su respuesta se encapsula como ``RespuestaJourney``.

        Args:
            solicitud:
                Solicitud protegida que contiene método HTTP, ruta, cadena de
                consulta, cabeceras, cuerpo e identificador de solicitud.

        Returns:
            ``ResultadoJourney`` con una ``RespuestaJourney`` cuando se obtiene
            respuesta del servicio, o con un ``FalloDependencia`` cuando ocurre
            un error de transporte.
        """
        # Solo se propagan hacia Journey las cabeceras expresamente permitidas.
        # ``title()`` permite realizar la comparación independientemente de la
        # capitalización con la que Flask haya recibido cada cabecera.
        h = {
            k: v
            for k, v in solicitud.cabeceras.items()
            if k.title() in CABECERAS_JOURNEY
        }

        # Se añade el identificador generado por la pasarela para permitir
        # correlacionar la solicitud entre ambos servicios.
        h["request_id"] = solicitud.id_solicitud

        escenario = solicitud.cabeceras.get("X-Escenario-Stub", "")

        # Un escenario stub solo se reenvía cuando la funcionalidad está
        # habilitada y el escenario pertenece específicamente a Journey.
        if self.permitir_escenario_stub and escenario.startswith("journey-"):
            h["X-Escenario-Stub"] = escenario

        # Reconstruye la URL del servicio conservando la ruta original.
        url = self.url + "/" + solicitud.ruta

        # La cadena de consulta se conserva únicamente cuando existe.
        if solicitud.cadena_consulta:
            url += "?" + solicitud.cadena_consulta

        r = self.transporte.solicitar(
            solicitud.metodo,
            url,
            cabeceras=h,
            cuerpo=solicitud.cuerpo,
            tiempo_espera_ms=self.tiempo_espera_ms
        )

        # Los errores de red o transporte se normalizan al modelo común
        # utilizado por la pasarela para representar fallos de dependencias.
        if isinstance(r, FalloTransporte):
            return ResultadoJourney(
                fallo=FalloDependencia(
                    "journey",
                    r.categoria,
                    r.latencia_ms,
                    r.categoria
                )
            )

        # La respuesta de Journey conserva estado y cuerpo, pero solo propaga
        # hacia el consumidor las cabeceras incluidas en la lista permitida.
        return ResultadoJourney(
            respuesta=RespuestaJourney(
                r.estado,
                r.cuerpo,
                {
                    k: v
                    for k, v in r.cabeceras.items()
                    if k.title() in CABECERAS_RESPUESTA
                }
            )
        )