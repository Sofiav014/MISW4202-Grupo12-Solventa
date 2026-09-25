from datetime import datetime

import jwt


class ServicioJwt:
    """Emite y valida tokens JWT ligados a session_id/device_id (PyJWT)."""

    def __init__(self, secreto: str, algoritmo: str = "HS256"):
        """Guarda el secreto y algoritmo usados para firmar y verificar los tokens."""
        self.secreto, self.algoritmo = secreto, algoritmo

    def emitir(self, session_id: str, device_id: str, expira_en: datetime) -> str:
        """Firma un JWT con session_id, device_id y expiración, y devuelve el token codificado."""
        payload = {"session_id": session_id, "device_id": device_id, "exp": expira_en}
        return jwt.encode(payload, self.secreto, algorithm=self.algoritmo)

    def validar(self, token: str) -> tuple[dict | None, str | None]:
        """Decodifica y verifica el token; devuelve (payload, None) o (None, código_de_error)."""
        try:
            return jwt.decode(token, self.secreto, algorithms=[self.algoritmo]), None
        except jwt.ExpiredSignatureError:
            return None, "token_expirado"
        except jwt.PyJWTError:
            return None, "token_invalido"
