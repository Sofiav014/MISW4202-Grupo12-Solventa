from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .modelos import Base


class AlmacenSesiones:
    """Encapsula el motor SQLAlchemy/SQLite utilizado para persistir sesiones."""

    def __init__(self, url_base_datos: str):
        """Crea el motor SQLAlchemy apuntando a `url_base_datos` y garantiza el esquema."""
        argumentos_conexion = {"check_same_thread": False} if url_base_datos.startswith("sqlite") else {}
        self.motor = create_engine(url_base_datos, connect_args=argumentos_conexion)
        Base.metadata.create_all(self.motor)
        self._fabrica_sesion = sessionmaker(bind=self.motor)

    @contextmanager
    def abrir(self):
        """Abre una sesión SQLAlchemy que hace commit al finalizar el bloque `with` y siempre se cierra."""
        bd = self._fabrica_sesion()
        try:
            yield bd
            bd.commit()
        finally:
            bd.close()
