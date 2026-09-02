"""Generación determinista de perfiles para el mock de Open Finance."""

import hashlib
from datetime import datetime, timezone
from typing import TypedDict


class PerfilFinancieroDTO(TypedDict):
    """Contrato de datos financieros expuesto por el proveedor simulado."""

    cliente_id: str
    score_riesgo: int
    fuente: str
    timestamp_perfil: str


class GeneradorDatosFinancieros:
    """Genera scores estables sin depender de persistencia externa."""

    @staticmethod
    def _valor_entero(digest: bytes, inicio: int, limite: int) -> int:
        """Convierte un bloque del hash en un entero dentro de un rango inclusivo."""
        bloque = int.from_bytes(digest[inicio : inicio + 4], byteorder="big")
        return bloque % (limite + 1)

    def obtenerPerfil(self, cliente_id: str) -> PerfilFinancieroDTO:
        """Obtiene un perfil para un identificador de cliente válido."""
        if not isinstance(cliente_id, str):
            raise TypeError("cliente_id debe ser un String")
        if not cliente_id.strip():
            raise ValueError("cliente_id no puede estar vacío")

        digest = hashlib.sha256(cliente_id.encode("utf-8")).digest()
        timestamp_perfil = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )

        return {
            "cliente_id": cliente_id,
            "score_riesgo": self._valor_entero(digest, inicio=0, limite=1000),
            "fuente": "OPEN_FINANCE",
            "timestamp_perfil": timestamp_perfil,
        }
