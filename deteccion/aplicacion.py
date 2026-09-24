from datetime import datetime
import time

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .auditoria import RegistradorAuditoriaJson
from .cliente_identidad import ClienteIdentidad, DatosSesion
from .configuracion import ConfiguracionDeteccion
from .modelos import ContextoEvaluacion, LineaBase
from .persistencia import AlmacenClientes
from .regla import ReglaDecision
from .repositorio import RepositorioClientes


# Campos que el Gateway envía siempre. La solicitud puede traer otros
# —request_id, por ejemplo— y se ignoran en lugar de rechazarse: exigir un
# conjunto exacto obligaría a desplegar los dos servicios al unísono cada vez
# que uno de ellos añadiera un campo de trazabilidad.
CAMPOS_REQUERIDOS = ("session_id", "device_id", "geo_lat", "geo_lon", "timestamp")


def crear_aplicacion(
    configuracion: ConfiguracionDeteccion,
    *,
    repositorio=None,
    cliente_identidad=None,
    auditoria=None,
) -> Flask:
    """
    Crea la aplicación Flask de ms-deteccion-sesiones.

    Expone IDetecciónSesión (``POST /evaluar``), que el Gateway consulta para
    decidir si una solicitud continúa hacia el journey, y consume
    IValidarSesión de ms-identidad-sesiones para conocer el dispositivo
    registrado del cliente.

    Args:
        configuracion:
            Umbrales, URL de Identidad y ruta de la base de datos.

        repositorio:
            Repositorio alternativo; las pruebas lo inyectan para trabajar
            sobre una base de datos temporal.

        cliente_identidad:
            Cliente alternativo de IValidarSesión, útil para simular que
            Identidad no responde.

        auditoria:
            Registrador alternativo de eventos.

    Returns:
        Aplicación Flask configurada.
    """
    aplicacion = Flask(__name__)

    registro = repositorio or RepositorioClientes(AlmacenClientes(configuracion.url_base_datos))
    identidad = cliente_identidad or ClienteIdentidad(
        configuracion.url_identidad,
        configuracion.tiempo_espera_identidad_ms,
    )
    regla = ReglaDecision(configuracion.radio_ubicacion_km, configuracion.ventana_actividad_min)
    bitacora = auditoria or RegistradorAuditoriaJson()

    @aplicacion.post("/evaluar")
    def evaluar():
        """IDetecciónSesión: evalúa el contexto de una sesión y emite un veredicto."""
        inicio = time.monotonic()

        datos = request.get_json(silent=True)
        contexto = _analizar(datos)

        if contexto is None:
            return jsonify(
                error_code="solicitud_invalida",
                message="The session context is invalid.",
            ), 400

        # La línea base combina lo que sabe Identidad sobre el dispositivo con
        # lo que el Detector observó sobre la ubicación.
        historial = registro.obtener(contexto.session_id)
        respuesta_identidad = identidad.validar_sesion(contexto.session_id)
        identidad_disponible = isinstance(respuesta_identidad, DatosSesion)

        if not identidad_disponible:
            bitacora.registrar(
                "identidad_degradada",
                session_id=contexto.session_id,
                request_id=_request_id(datos),
                categoria=respuesta_identidad.categoria,
                latencia_ms=round(respuesta_identidad.latencia_ms, 3),
            )

        linea_base = _linea_base(respuesta_identidad, historial, identidad_disponible)
        veredicto = regla.evaluar(contexto, linea_base)

        # Solo una solicitud considerada legítima actualiza la referencia de
        # ubicación; en caso contrario un suplantador desplazaría la línea
        # base hacia sí mismo y su siguiente intento parecería normal.
        if veredicto.veredicto == "normal":
            registro.anotar_ubicacion(
                contexto.session_id,
                contexto.geo_lat,
                contexto.geo_lon,
                contexto.instante,
            )

        latencia_ms = (time.monotonic() - inicio) * 1000

        bitacora.registrar(
            "evaluacion",
            session_id=contexto.session_id,
            request_id=_request_id(datos),
            veredicto=veredicto.veredicto,
            motivo=veredicto.motivo,
            score_riesgo=veredicto.score_riesgo,
            latencia_ms=round(latencia_ms, 3),
            presupuesto_ms=configuracion.presupuesto_latencia_ms,
            dentro_de_presupuesto=latencia_ms <= configuracion.presupuesto_latencia_ms,
            identidad_disponible=identidad_disponible,
        )

        return jsonify(veredicto.a_dict())

    @aplicacion.errorhandler(Exception)
    def manejar_excepcion(error):
        """Devuelve cualquier fallo no controlado en la misma forma JSON que el resto de la API."""
        if isinstance(error, HTTPException):
            return error

        aplicacion.logger.exception("unexpected detector exception")

        return jsonify(
            error_code="deteccion_error_interno",
            message="The detector could not complete the request.",
        ), 500

    return aplicacion


def _analizar(datos) -> ContextoEvaluacion | None:
    """
    Valida el contexto recibido y lo convierte a tipos internos.

    Devuelve ``None`` si falta algún campo requerido o si alguno no cumple el
    contrato; los campos adicionales se ignoran deliberadamente.
    """
    if not isinstance(datos, dict):
        return None

    if any(campo not in datos for campo in CAMPOS_REQUERIDOS):
        return None

    session_id, device_id = datos["session_id"], datos["device_id"]

    if not _es_texto(session_id) or not _es_texto(device_id):
        return None

    try:
        lat, lon = _numero(datos["geo_lat"]), _numero(datos["geo_lon"])

        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            return None

        instante = datetime.fromisoformat(str(datos["timestamp"]).replace("Z", "+00:00"))

        if instante.tzinfo is None:
            return None

    except (ValueError, TypeError, OverflowError):
        return None

    return ContextoEvaluacion(session_id, device_id, lat, lon, instante)


def _linea_base(respuesta_identidad, historial, identidad_disponible) -> LineaBase:
    """
    Construye la línea base a partir de Identidad y del historial local.

    Cuando Identidad no responde se degrada al dispositivo que el Detector
    tenga registrado. Si tampoco hay registro local, la línea base queda sin
    dispositivo y la regla resuelve de forma conservadora.
    """
    if identidad_disponible and respuesta_identidad.device_id_registrado:
        device_id = respuesta_identidad.device_id_registrado
    elif historial:
        device_id = historial["device_id_registrado"]
    else:
        device_id = None

    return LineaBase(
        device_id_registrado=device_id,
        ultima_lat=historial["ultima_lat"] if historial else None,
        ultima_lon=historial["ultima_lon"] if historial else None,
        ultima_actividad=historial["ultima_actividad"] if historial else None,
        identidad_disponible=identidad_disponible,
    )


def _request_id(datos) -> str | None:
    """Recupera el identificador de correlación que envía el Gateway, si viene."""
    return datos.get("request_id") if isinstance(datos, dict) else None


def _es_texto(valor) -> bool:
    """Indica si `valor` es un string no vacío."""
    return isinstance(valor, str) and bool(valor.strip())


def _numero(valor) -> float:
    """Convierte a float rechazando booleanos y valores no finitos."""
    if isinstance(valor, bool):
        raise ValueError("boolean is not numeric")

    numero = float(valor)

    if numero != numero:
        raise ValueError("not finite")

    return numero
