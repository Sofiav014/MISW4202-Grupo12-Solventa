from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .modelos import Base


class AlmacenClientes:
    """Encapsula el motor SQLAlchemy/SQLite donde vive el historial de clientes."""

    def __init__(self, url_base_datos: str):
        """Crea el motor apuntando a `url_base_datos` y garantiza el esquema."""
        argumentos_conexion = (
            {"check_same_thread": False} if url_base_datos.startswith("sqlite") else {}
        )
        self.motor = create_engine(url_base_datos, connect_args=argumentos_conexion)

        if url_base_datos.startswith("sqlite"):
            self._configurar_sqlite()

        Base.metadata.create_all(self.motor)
        self._fabrica_sesion = sessionmaker(bind=self.motor)

    def _configurar_sqlite(self) -> None:
        """
        Ajusta SQLite para soportar la concurrencia de las corridas de carga.

        WAL permite que las lecturas no bloqueen a la escritura, y el
        ``busy_timeout`` evita que una escritura simultánea falle de inmediato
        con "database is locked" en lugar de esperar su turno.
        """

        @event.listens_for(self.motor, "connect")
        def _ajustar(conexion, _registro):
            cursor = conexion.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    @contextmanager
    def abrir(self):
        """Abre una sesión que hace commit al salir del bloque y siempre se cierra."""
        bd = self._fabrica_sesion()
        try:
            yield bd
            bd.commit()
        finally:
            bd.close()
