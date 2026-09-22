from datetime import datetime, timedelta, timezone
import uuid

from .modelos import (
    ESTADO_ACTIVA,
    ESTADO_EXPIRADA,
    ESTADO_PENDIENTE_VERIFICACION,
    ESTADO_REVOCADA,
    InstantaneaSesion,
    ResultadoVerificacion,
    Sesion,
)


def _ahora() -> datetime:
    """Devuelve el instante actual con zona horaria UTC."""
    return datetime.now(timezone.utc)


def _a_naive_utc(instante: datetime | None) -> datetime | None:
    """Convierte un datetime con zona horaria a UTC naive, listo para guardarse en SQLite."""
    return instante.astimezone(timezone.utc).replace(tzinfo=None) if instante else None


def _a_tz_aware(instante: datetime | None) -> datetime | None:
    """Reasigna la zona horaria UTC a un datetime naive leído desde SQLite."""
    return instante.replace(tzinfo=timezone.utc) if instante else None


class RepositorioSesiones:
    """
    Gestiona el ciclo de vida de las sesiones: creación, verificación reforzada,
    revocación y expiración perezosa (evaluada al consultar, sin proceso aparte).
    """

    def __init__(self, almacen):
        """Recibe el `AlmacenSesiones` que provee las sesiones SQLAlchemy."""
        self.almacen = almacen

    def crear(self, session_id: str, device_id: str, ttl_segundos: int) -> InstantaneaSesion:
        """Crea o reemplaza una sesión activa ligada a `device_id`, con vencimiento en `ttl_segundos`."""
        ahora = _ahora()
        expira_en = ahora + timedelta(seconds=ttl_segundos)

        with self.almacen.abrir() as bd:
            sesion = bd.get(Sesion, session_id) or Sesion(session_id=session_id, creada_en=_a_naive_utc(ahora))
            sesion.device_id_registrado = device_id
            sesion.estado = ESTADO_ACTIVA
            sesion.ultima_actividad = _a_naive_utc(ahora)
            sesion.expira_en = _a_naive_utc(expira_en)
            sesion.referencia_verificacion = None
            bd.add(sesion)
            bd.flush()
            return self._instantanea(sesion)

    def obtener(self, session_id: str) -> InstantaneaSesion | None:
        """Devuelve el estado actual de la sesión, expirándola primero si su ttl ya venció."""
        with self.almacen.abrir() as bd:
            sesion = bd.get(Sesion, session_id)
            if sesion is None:
                return None

            self._expirar_si_corresponde(sesion)
            bd.add(sesion)
            return self._instantanea(sesion)

    def marcar_pendiente_verificacion(self, session_id: str) -> ResultadoVerificacion:
        """Marca la sesión como pendiente de verificación, creándola si no existía aún."""
        ahora = _a_naive_utc(_ahora())

        with self.almacen.abrir() as bd:
            sesion = bd.get(Sesion, session_id)
            ya_estaba_pendiente = bool(
                sesion and sesion.estado == ESTADO_PENDIENTE_VERIFICACION and sesion.referencia_verificacion
            )

            if sesion is None:
                sesion = Sesion(session_id=session_id, creada_en=ahora)

            sesion.estado = ESTADO_PENDIENTE_VERIFICACION
            sesion.ultima_actividad = ahora

            # Mientras la verificación siga pendiente, se conserva la misma
            # referencia para no generar una nueva por cada intento repetido.
            if not ya_estaba_pendiente:
                sesion.referencia_verificacion = f"ref-{uuid.uuid4()}"

            bd.add(sesion)
            bd.flush()
            return ResultadoVerificacion(sesion.referencia_verificacion, ya_estaba_pendiente)

    def revocar(self, session_id: str) -> InstantaneaSesion | None:
        """Revoca la sesión de forma inmediata; devuelve `None` si no existe."""
        with self.almacen.abrir() as bd:
            sesion = bd.get(Sesion, session_id)
            if sesion is None:
                return None

            sesion.estado = ESTADO_REVOCADA
            sesion.ultima_actividad = _a_naive_utc(_ahora())
            bd.add(sesion)
            bd.flush()
            return self._instantanea(sesion)

    @staticmethod
    def _expirar_si_corresponde(sesion: Sesion) -> None:
        """Transiciona la sesión a expirada in-place si estaba activa y su ttl ya venció."""
        if sesion.estado == ESTADO_ACTIVA and sesion.expira_en and sesion.expira_en <= _a_naive_utc(_ahora()):
            sesion.estado = ESTADO_EXPIRADA

    @staticmethod
    def _instantanea(sesion: Sesion) -> InstantaneaSesion:
        """Convierte una fila `Sesion` de SQLAlchemy en un `InstantaneaSesion` inmutable."""
        return InstantaneaSesion(
            sesion.session_id,
            sesion.device_id_registrado,
            sesion.estado,
            _a_tz_aware(sesion.ultima_actividad),
            _a_tz_aware(sesion.expira_en),
            sesion.referencia_verificacion,
        )
