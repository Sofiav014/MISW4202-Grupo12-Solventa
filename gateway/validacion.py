from datetime import datetime
import json
from typing import Any

import jwt

from .modelos import ContextoSesion, FalloValidacion


class ValidadorPyJwt:
    """
    Valida la autenticación de una solicitud mediante un token JWT.

    El token se espera en la cabecera HTTP ``Authorization`` utilizando
    el esquema Bearer:

        Authorization: Bearer <token>

    La validación comprueba que el token exista y que pueda ser verificado
    utilizando el secreto y algoritmo configurados.
    """

    def __init__(self, secreto: str, algoritmo: str = "HS256"):
        """
        Inicializa el validador JWT.

        Args:
            secreto:
                Clave utilizada para verificar la firma del token JWT.

            algoritmo:
                Algoritmo criptográfico esperado para verificar el token.
                Por defecto se utiliza HS256.
        """
        self.secreto = secreto
        self.algoritmo = algoritmo

    def validar(self, cabeceras: dict[str, str]) -> FalloValidacion | None:
        """
        Valida el token JWT presente en las cabeceras de una solicitud.

        La función verifica primero que exista una cabecera Authorization
        válida con el esquema Bearer. Posteriormente intenta decodificar
        y verificar el JWT mediante PyJWT.

        Args:
            cabeceras:
                Diccionario con las cabeceras HTTP de la solicitud.

        Returns:
            ``None`` si el token es válido.

            ``FalloValidacion("auth_required")`` si no se suministra un
            token Bearer válido.

            ``FalloValidacion("auth_invalid")`` si PyJWT no puede validar
            o decodificar el token.
        """
        autorizacion = cabeceras.get("Authorization", "")

        # El Gateway exige explícitamente un token Bearer no vacío.
        if not autorizacion.startswith("Bearer ") or not autorizacion[7:].strip():
            return FalloValidacion("auth_required")

        try:
            # Se elimina el prefijo "Bearer " y se verifica la firma y
            # demás restricciones estándar procesadas por PyJWT.
            jwt.decode(
                autorizacion[7:].strip(),
                self.secreto,
                algorithms=[self.algoritmo],
            )
        except jwt.PyJWTError as error:
            # Cualquier error de PyJWT se traduce a un fallo uniforme
            # para evitar que las capas superiores dependan de PyJWT.
            return FalloValidacion("auth_invalid", str(error))

        return None


class AnalizadorContextoSesion:
    """
    Analiza y valida el contexto de sesión enviado por una solicitud.

    El contexto debe llegar serializado como JSON dentro de la cabecera
    ``X-Session-Context`` y contener exactamente los siguientes campos:

        - session_id
        - device_id
        - geo_lat
        - geo_lon
        - timestamp

    Si el contexto es válido, se transforma en una instancia de
    ``ContextoSesion``. En caso contrario se devuelve un
    ``FalloValidacion``.
    """

    campos = {"session_id", "device_id", "geo_lat", "geo_lon", "timestamp"}

    def analizar(
        self,
        cabeceras: dict[str, str],
    ) -> ContextoSesion | FalloValidacion:
        """
        Convierte la cabecera X-Session-Context en un ContextoSesion validado.

        Se validan:

        - existencia de la cabecera;
        - formato JSON;
        - conjunto exacto de campos;
        - identificadores de sesión y dispositivo no vacíos;
        - latitud y longitud numéricas;
        - rangos geográficos válidos;
        - timestamp ISO 8601;
        - presencia explícita de zona horaria.

        Args:
            cabeceras:
                Diccionario con las cabeceras HTTP de la solicitud.

        Returns:
            ``ContextoSesion`` cuando todos los datos son válidos.

            ``FalloValidacion`` cuando la cabecera falta o alguno de sus
            valores no cumple el contrato esperado.
        """
        valor = cabeceras.get("X-Session-Context")

        if not valor:
            return FalloValidacion(
                "invalid_session_context",
                "missing header",
            )

        try:
            # La cabecera contiene un objeto JSON serializado.
            datos: Any = json.loads(valor)

            # El contrato exige un objeto y exactamente estos campos:
            # no pueden faltar campos ni incluirse campos adicionales.
            if not isinstance(datos, dict) or set(datos) != self.campos:
                raise ValueError("invalid fields")

            # Valida los identificadores textuales requeridos.
            sesion = self._texto(datos["session_id"])
            dispositivo = self._texto(datos["device_id"])

            # Convierte y valida las coordenadas antes de comprobar
            # que correspondan a rangos geográficos posibles.
            latitud = self._numero(datos["geo_lat"])
            longitud = self._numero(datos["geo_lon"])

            if not -90 <= latitud <= 90 or not -180 <= longitud <= 180:
                raise ValueError("coordinates out of range")

            # Se acepta formato ISO 8601. Los timestamps terminados en "Z"
            # se convierten a su representación equivalente UTC (+00:00)
            # para que datetime.fromisoformat pueda interpretarlos.
            instante = datetime.fromisoformat(
                str(datos["timestamp"]).replace("Z", "+00:00")
            )

            # El timestamp debe indicar explícitamente una zona horaria.
            # Un datetime "naive" no permite establecer inequívocamente
            # cuándo ocurrió el evento.
            if instante.tzinfo is None:
                raise ValueError("timestamp must include timezone")

            return ContextoSesion(
                sesion,
                dispositivo,
                latitud,
                longitud,
                instante,
            )

        except (ValueError, TypeError, json.JSONDecodeError, OverflowError):
            # Los distintos errores internos se normalizan bajo un único
            # error de contrato para el consumidor de esta clase.
            return FalloValidacion(
                "invalid_session_context",
                "malformed or impossible context",
            )

    @staticmethod
    def _texto(valor: Any) -> str:
        """
        Valida que un valor sea texto no vacío.

        Args:
            valor:
                Valor que debe representar un campo textual requerido.

        Returns:
            El mismo texto recibido cuando es válido.

        Raises:
            ValueError:
                Si el valor no es un string o contiene únicamente espacios.
        """
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError("empty text")

        return valor

    @staticmethod
    def _numero(valor: Any) -> float:
        """
        Convierte un valor a número decimal y valida que sea utilizable.

        Los booleanos se rechazan explícitamente porque en Python ``bool``
        es compatible con valores numéricos (True == 1, False == 0), pero
        no son valores válidos para las coordenadas del contexto.

        Args:
            valor:
                Valor que debe poder convertirse a ``float``.

        Returns:
            El valor convertido a ``float``.

        Raises:
            ValueError:
                Si el valor es booleano o produce un valor NaN.
        """
        if isinstance(valor, bool):
            raise ValueError("boolean is not numeric")

        numero = float(valor)

        # NaN es el único float que no es igual a sí mismo.
        if not (numero == numero):
            raise ValueError("not finite")

        return numero