from flask import Flask, jsonify, request

from .configuracion import ConfiguracionIdentidad
from .jwt_servicio import ServicioJwt
from .modelos import ESTADO_ACTIVA, InstantaneaSesion
from .persistencia import AlmacenSesiones
from .repositorio import RepositorioSesiones


def crear_aplicacion(configuracion: ConfiguracionIdentidad, *, repositorio=None, servicio_jwt=None) -> Flask:
    """
    Crea la aplicación Flask de ms-identidad-sesiones.

    Expone:
    - ISesiones: creación de sesiones (con emisión de JWT), consulta y
      revocación.
    - IValidarSesión: consultada por el Detector para conocer el dispositivo
      registrado y la última actividad de una sesión.
    - IIdentidad: consultada por el Gateway para iniciar la verificación
      reforzada de una sesión sospechosa.
    """
    aplicacion = Flask(__name__)
    repositorio_sesiones = repositorio or RepositorioSesiones(AlmacenSesiones(configuracion.url_base_datos))
    jwt_servicio = servicio_jwt or ServicioJwt(configuracion.secreto_jwt, configuracion.algoritmo_jwt)

    @aplicacion.post("/sesiones")
    def crear_sesion():
        """ISesiones: crea/reemplaza una sesión activa y emite su JWT ligado a device_id."""
        datos = request.get_json(silent=True)
        if not isinstance(datos, dict):
            return _error("solicitud_invalida", "The request body must be a JSON object.", 400)

        session_id = datos.get("session_id")
        device_id = datos.get("device_id")
        if not _es_texto(session_id) or not _es_texto(device_id):
            return _error("solicitud_invalida", "session_id and device_id are required.", 400)

        ttl_segundos = datos.get("ttl_segundos", configuracion.ttl_sesion_segundos)
        if not isinstance(ttl_segundos, int) or isinstance(ttl_segundos, bool):
            return _error("solicitud_invalida", "ttl_segundos must be an integer.", 400)

        instantanea = repositorio_sesiones.crear(session_id, device_id, ttl_segundos)
        token = jwt_servicio.emitir(instantanea.session_id, instantanea.device_id_registrado, instantanea.expira_en)

        return jsonify(session_id=instantanea.session_id, estado=instantanea.estado, token=token,
                        expira_en=_a_iso(instantanea.expira_en)), 201

    @aplicacion.get("/sesiones/<session_id>")
    def consultar_sesion(session_id):
        """ISesiones: devuelve el estado actual de una sesión."""
        instantanea = repositorio_sesiones.obtener(session_id)
        if instantanea is None:
            return _error("sesion_no_encontrada", "The session does not exist.", 404)

        return jsonify(_a_dict(instantanea))

    @aplicacion.post("/sesiones/<session_id>/revocar")
    def revocar_sesion(session_id):
        """ISesiones: revoca una sesión con efecto inmediato en consultas posteriores."""
        instantanea = repositorio_sesiones.revocar(session_id)
        if instantanea is None:
            return _error("sesion_no_encontrada", "The session does not exist.", 404)

        return jsonify(_a_dict(instantanea))

    @aplicacion.post("/sesiones/validar")
    def validar_sesion():
        """IValidarSesión: usada por el Detector para consultar dispositivo registrado y última actividad."""
        datos = request.get_json(silent=True)
        session_id = datos.get("session_id") if isinstance(datos, dict) else None
        if not _es_texto(session_id):
            return _error("solicitud_invalida", "session_id is required.", 400)

        instantanea = repositorio_sesiones.obtener(session_id)
        if instantanea is None:
            instantanea = InstantaneaSesion(session_id, None, "desconocida", None, None)

        cuerpo = _a_dict(instantanea)
        cuerpo["sesion_activa"] = instantanea.estado == ESTADO_ACTIVA
        return jsonify(cuerpo)

    @aplicacion.post("/verificacion/iniciar")
    def iniciar_verificacion():
        """IIdentidad: usada por el Gateway para iniciar la verificación reforzada de una sesión sospechosa."""
        datos = request.get_json(silent=True)
        session_id = datos.get("session_id") if isinstance(datos, dict) else None
        if not _es_texto(session_id):
            return _error("solicitud_invalida", "session_id is required.", 400)

        resultado = repositorio_sesiones.marcar_pendiente_verificacion(session_id)
        estado_respuesta = "pending" if resultado.ya_estaba_pendiente else "verification_started"
        return jsonify(status=estado_respuesta, reference=resultado.referencia)

    @aplicacion.post("/jwt/validar")
    def validar_token():
        """Valida un JWT emitido por este servicio y devuelve sus claims si es válido."""
        datos = request.get_json(silent=True)
        token = datos.get("token") if isinstance(datos, dict) else None
        if not _es_texto(token):
            return _error("solicitud_invalida", "token is required.", 400)

        payload, error = jwt_servicio.validar(token)
        if error:
            return jsonify(valido=False, error_code=error), 401

        return jsonify(valido=True, session_id=payload.get("session_id"), device_id=payload.get("device_id"))

    return aplicacion


def _es_texto(valor) -> bool:
    """Indica si `valor` es un string no vacío (tras quitar espacios)."""
    return isinstance(valor, str) and bool(valor.strip())


def _a_iso(instante) -> str | None:
    """Convierte un datetime a ISO 8601, o `None` si no hay valor."""
    return instante.isoformat() if instante else None


def _a_dict(instantanea: InstantaneaSesion) -> dict:
    """Serializa un `InstantaneaSesion` al formato JSON expuesto por la API."""
    return {
        "session_id": instantanea.session_id,
        "estado": instantanea.estado,
        "device_id_registrado": instantanea.device_id_registrado,
        "ultima_actividad": _a_iso(instantanea.ultima_actividad),
        "expira_en": _a_iso(instantanea.expira_en),
    }


def _error(codigo: str, mensaje: str, estado_http: int):
    """Construye una respuesta JSON de error estable con código, mensaje y estado HTTP."""
    return jsonify(error_code=codigo, message=mensaje), estado_http
