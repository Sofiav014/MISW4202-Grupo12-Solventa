"""Generación determinista de perfiles para el mock de Open Finance."""

import hashlib
from typing import TypedDict


class PerfilFinancieroDTO(TypedDict):
    """Contrato de datos financieros expuesto por el proveedor simulado."""

    clienteId: str
    puntajeEstabilidadIngresos: int
    relacionDeudaIngresos: float
    puntajeComportamientoPago: int
    incumplimientos12Meses: int
    periodoInformacion: str
    fechaVigenciaDatos: str


class GeneradorDatosFinancieros:
    """Genera perfiles estables sin depender de persistencia externa."""

    PERIODO_INFORMACION = "2025-01-01/2025-12-31"
    FECHA_VIGENCIA_DATOS = "2026-12-31"

    """Son @staticmethod porque esas dos funciones no necesitan acceder al estado de la instancia (self) ni al estado de la clase (cls)."""
    @staticmethod
    def _valor_entero(digest: bytes, inicio: int, limite: int) -> int:
        """Convierte un bloque del hash en un entero dentro de un rango inclusivo."""
        bloque = int.from_bytes(digest[inicio : inicio + 4], byteorder="big")
        return bloque % (limite + 1)

    @staticmethod
    def _maximo_incumplimientos(puntaje_pago: int) -> int:
        """Acota incumplimientos según el comportamiento de pago."""
        if puntaje_pago >= 80:
            return 0
        if puntaje_pago >= 60:
            return 1
        if puntaje_pago >= 40:
            return 2
        if puntaje_pago >= 20:
            return 3
        return 5

    def obtenerPerfil(self, clienteId: str) -> PerfilFinancieroDTO:
        """Obtiene siempre el mismo perfil para un identificador de cliente válido."""
        if not isinstance(clienteId, str):
            raise TypeError("clienteId debe ser un String")
        if not clienteId.strip():
            raise ValueError("clienteId no puede estar vacío")

        digest = hashlib.sha256(clienteId.encode("utf-8")).digest()
        puntaje_pago = self._valor_entero(digest, inicio=8, limite=100)
        maximo_incumplimientos = self._maximo_incumplimientos(puntaje_pago)

        return {
            "clienteId": clienteId,
            "puntajeEstabilidadIngresos": self._valor_entero(
                digest, inicio=0, limite=100
            ),
            "relacionDeudaIngresos": round(
                self._valor_entero(digest, inicio=4, limite=100) / 100,
                2,
            ),
            "puntajeComportamientoPago": puntaje_pago,
            "incumplimientos12Meses": self._valor_entero(
                digest,
                inicio=12,
                limite=maximo_incumplimientos,
            ),
            "periodoInformacion": self.PERIODO_INFORMACION,
            "fechaVigenciaDatos": self.FECHA_VIGENCIA_DATOS,
        }
