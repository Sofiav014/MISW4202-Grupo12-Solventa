import time

from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class RespuestaHttpCruda:
    """
    Representa una respuesta HTTP recibida correctamente a nivel de transporte.

    Esta estructura no interpreta el contenido de la respuesta ni aplica
    reglas de negocio. Conserva únicamente la información HTTP necesaria para
    que los clientes de servicios externos puedan procesarla posteriormente.

    Attributes:
        estado:
            Código de estado HTTP devuelto por el servicio remoto.

        cabeceras:
            Cabeceras HTTP incluidas en la respuesta.

        cuerpo:
            Contenido bruto de la respuesta representado como bytes.

        latencia_ms:
            Tiempo total empleado por la operación HTTP, expresado en
            milisegundos.
    """

    estado: int
    cabeceras: dict[str, str]
    cuerpo: bytes
    latencia_ms: float


@dataclass(frozen=True)
class FalloTransporte:
    """
    Representa un fallo ocurrido durante una operación HTTP.

    Permite que las capas superiores trabajen con una representación estable
    de los errores de red sin depender directamente de las excepciones
    específicas de la librería ``requests``.

    Attributes:
        categoria:
            Tipo de fallo detectado por la capa de transporte.

        latencia_ms:
            Tiempo transcurrido desde el inicio de la operación hasta que se
            detectó el fallo, expresado en milisegundos.
    """

    categoria: str
    latencia_ms: float


class TransporteHttp:
    """
    Implementa la comunicación HTTP utilizada por los clientes externos.

    Esta clase encapsula ``requests`` y normaliza sus resultados en dos
    posibles estructuras:

    - ``RespuestaHttpCruda`` cuando se recibe una respuesta HTTP;
    - ``FalloTransporte`` cuando ocurre un timeout o un error de conexión.

    La capa de transporte no interpreta códigos HTTP ni cuerpos de respuesta.
    Esa responsabilidad corresponde al cliente de cada dependencia.
    """

    def solicitar(self, metodo, url, *, cabeceras=None, cuerpo=b"", tiempo_espera_ms=500):
        """
        Ejecuta una solicitud HTTP y mide el tiempo empleado por la operación.

        Args:
            metodo:
                Método HTTP que se utilizará para realizar la solicitud.

            url:
                Dirección completa del recurso remoto.

            cabeceras:
                Cabeceras HTTP opcionales que deben enviarse con la solicitud.

            cuerpo:
                Cuerpo de la solicitud en formato bytes.

            tiempo_espera_ms:
                Tiempo máximo de espera para la operación, expresado en
                milisegundos.

        Returns:
            ``RespuestaHttpCruda`` cuando el servicio devuelve una respuesta,
            independientemente de su código de estado HTTP.

            ``FalloTransporte`` con categoría ``tiempo_espera`` cuando la
            operación supera el timeout configurado.

            ``FalloTransporte`` con categoría ``conexion`` cuando ``requests``
            produce cualquier otro error de transporte.
        """
        # Se utiliza un reloj monotónico para medir duración, ya que no se ve
        # afectado por ajustes del reloj del sistema durante la solicitud.
        inicio = time.monotonic()

        try:
            # requests utiliza segundos para el timeout, por lo que el valor
            # configurado en milisegundos se convierte antes de realizar
            # la llamada.
            r = requests.request(
                metodo,
                url,
                headers=cabeceras or {},
                data=cuerpo,
                timeout=tiempo_espera_ms / 1000
            )

            # Recibir un código HTTP de error, por ejemplo 404 o 500, sigue
            # siendo una comunicación válida a nivel de transporte. La
            # interpretación del estado corresponde al cliente consumidor.
            return RespuestaHttpCruda(
                r.status_code,
                dict(r.headers),
                r.content,
                (time.monotonic() - inicio) * 1000
            )

        except requests.Timeout:
            # Los timeouts se distinguen del resto de errores de transporte
            # porque suelen requerir políticas diferentes en capas superiores.
            return FalloTransporte(
                "tiempo_espera",
                (time.monotonic() - inicio) * 1000
            )

        except requests.RequestException:
            # Cualquier otro fallo manejado por requests se normaliza como
            # un problema de conexión, evitando propagar excepciones externas.
            return FalloTransporte(
                "conexion",
                (time.monotonic() - inicio) * 1000
            )