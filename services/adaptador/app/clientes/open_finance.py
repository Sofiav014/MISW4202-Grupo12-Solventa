"""Cliente HTTP del proveedor externo Open Finance.

Traduce cualquier falla del proveedor a OpenFinanceError y no reintenta, para
que el Circuit Breaker que envuelve a este cliente pueda contar las fallas.
"""
import requests

from app.config import MOCK_URL, TIMEOUT_MS

# Tupla y no escalar: requests aplicaría un escalar a connect y a read por
# separado, permitiendo esperas de hasta el doble del presupuesto.
CONNECT_TIMEOUT_S = 0.2
READ_TIMEOUT_S = TIMEOUT_MS / 1000
REQUEST_TIMEOUT = (CONNECT_TIMEOUT_S, READ_TIMEOUT_S)

CAMPOS_PERFIL_REQUERIDOS = ("cliente_id", "score_riesgo", "fuente", "timestamp_perfil")


class OpenFinanceError(Exception):
    """Falla al invocar Open Finance.

    `tipo` es "timeout", "conexion" o "respuesta_invalida", el vocabulario que
    la instrumentación registra en la columna tipo_error.
    """

    def __init__(self, tipo: str, mensaje: str):
        self.tipo = tipo
        self.mensaje = mensaje
        super().__init__(mensaje)


class OpenFinanceClient:
    """Cliente del proveedor Open Finance."""

    def __init__(self, base_url: str = MOCK_URL, timeout: tuple = REQUEST_TIMEOUT):
        self.base_url = base_url
        self.timeout = timeout

    def obtener_perfil(self, cliente_id: str) -> dict:
        """Devuelve el perfil del cliente, o lanza OpenFinanceError."""
        try:
            respuesta = requests.get(
                f"{self.base_url}/perfil/{cliente_id}", timeout=self.timeout
            )
        except requests.exceptions.Timeout as exc:
            raise OpenFinanceError(
                "timeout", f"Open Finance no respondió en {TIMEOUT_MS} ms"
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise OpenFinanceError(
                "conexion", "No se pudo establecer conexión con Open Finance"
            ) from exc

        return self._validar_perfil(respuesta)

    def _validar_perfil(self, respuesta) -> dict:
        """Verifica código HTTP, cuerpo JSON y contrato mínimo del perfil."""
        if respuesta.status_code != 200:
            raise OpenFinanceError(
                "respuesta_invalida",
                f"Open Finance respondió HTTP {respuesta.status_code}",
            )

        try:
            perfil = respuesta.json()
        except ValueError as exc:
            raise OpenFinanceError(
                "respuesta_invalida", "La respuesta de Open Finance no es JSON válido"
            ) from exc

        campos_faltantes = [c for c in CAMPOS_PERFIL_REQUERIDOS if c not in perfil]
        if campos_faltantes:
            raise OpenFinanceError(
                "respuesta_invalida",
                f"El perfil no cumple el contrato mínimo, faltan: {campos_faltantes}",
            )

        return perfil
