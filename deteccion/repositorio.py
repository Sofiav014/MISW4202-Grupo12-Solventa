from datetime import datetime, timezone
import threading

from .modelos import ClienteConocido


def _a_naive_utc(instante: datetime | None) -> datetime | None:
    """Convierte un datetime con zona horaria a UTC naive, para guardarlo en SQLite."""
    return instante.astimezone(timezone.utc).replace(tzinfo=None) if instante else None


def _a_tz_aware(instante: datetime | None) -> datetime | None:
    """Reasigna la zona horaria UTC a un datetime naive leído desde SQLite."""
    return instante.replace(tzinfo=timezone.utc) if instante else None


class RepositorioClientes:
    """
    Acceso al historial de dispositivos y ubicaciones conocidas por sesión.

    Es la fuente contra la que el Detector contrasta cada solicitud, y la que
    el script de semilla precarga para que existan clientes con los que
    comparar desde la primera petición.
    """

    def __init__(self, almacen):
        """Recibe el `AlmacenClientes` que provee las sesiones SQLAlchemy."""
        self.almacen = almacen
        self._cerrojo = threading.Lock()

    def registrar(
        self,
        session_id: str,
        cliente_id: str,
        device_id: str,
        lat: float | None = None,
        lon: float | None = None,
        instante: datetime | None = None,
    ) -> None:
        """Da de alta o actualiza por completo el registro de una sesión."""
        with self._cerrojo, self.almacen.abrir() as bd:
            registro = bd.get(ClienteConocido, session_id) or ClienteConocido(session_id=session_id)
            registro.cliente_id = cliente_id
            registro.device_id_registrado = device_id
            registro.ultima_lat = lat
            registro.ultima_lon = lon
            registro.ultima_actividad = _a_naive_utc(instante)
            bd.add(registro)

    def obtener(self, session_id: str) -> dict | None:
        """Devuelve el historial de una sesión, o ``None`` si no hay registro."""
        with self.almacen.abrir() as bd:
            registro = bd.get(ClienteConocido, session_id)

            if registro is None:
                return None

            return {
                "session_id": registro.session_id,
                "cliente_id": registro.cliente_id,
                "device_id_registrado": registro.device_id_registrado,
                "ultima_lat": registro.ultima_lat,
                "ultima_lon": registro.ultima_lon,
                "ultima_actividad": _a_tz_aware(registro.ultima_actividad),
            }

    def anotar_ubicacion(self, session_id: str, lat: float, lon: float, instante: datetime) -> None:
        """
        Actualiza la última ubicación conocida de una sesión existente.

        Solo se invoca tras un veredicto normal: anotar la ubicación de una
        solicitud que se consideró sospechosa movería la línea base hacia el
        propio suplantador y haría que su segundo intento pareciera legítimo.
        """
        with self._cerrojo, self.almacen.abrir() as bd:
            registro = bd.get(ClienteConocido, session_id)

            if registro is None:
                return

            registro.ultima_lat = lat
            registro.ultima_lon = lon
            registro.ultima_actividad = _a_naive_utc(instante)
            bd.add(registro)

    def contar(self) -> int:
        """Cuenta los clientes cargados; lo usa el script de semilla al reportar."""
        with self.almacen.abrir() as bd:
            return bd.query(ClienteConocido).count()

    def vaciar(self) -> None:
        """Elimina todo el historial para que la carga de semilla sea reproducible."""
        with self._cerrojo, self.almacen.abrir() as bd:
            bd.query(ClienteConocido).delete()
