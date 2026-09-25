"""
Integración de los tres servicios reales: Gateway, Detector e Identidad.

Las pruebas de `probar_gateway_con_stubs` verifican que el Gateway se comporta
bien frente a dobles deterministas. Estas verifican algo distinto y necesario
antes de la corrida del experimento: que los contratos acordados encajan entre
implementaciones reales, sin ninguna cabecera de simulación de por medio.
"""

from datetime import datetime, timedelta, timezone
import io
import json
from urllib.parse import urlsplit

import jwt
import pytest

from deteccion.aplicacion import crear_aplicacion as crear_deteccion
from deteccion.auditoria import RegistradorAuditoriaJson as AuditoriaDeteccion
from deteccion.cliente_identidad import ClienteIdentidad as ClienteIdentidadDeteccion
from deteccion.configuracion import ConfiguracionDeteccion
from deteccion.persistencia import AlmacenClientes
from deteccion.repositorio import RepositorioClientes
from dobles_http.journey_prueba import crear_aplicacion as crear_journey
from gateway.aplicacion import crear_aplicacion as crear_gateway
from gateway.clientes.detector import ClienteDetector
from gateway.clientes.identidad import ClienteIdentidad
from gateway.clientes.journey import ClienteHttpJourney
from gateway.clientes.transporte import RespuestaHttpCruda
from gateway.configuracion import ConfiguracionPasarela
from gateway.politica import PolicyGate
from gateway.validacion import AnalizadorContextoSesion, ValidadorPyJwt
from identidad.aplicacion import crear_aplicacion as crear_identidad
from identidad.configuracion import ConfiguracionIdentidad


SECRETO = "secreto-de-integracion"
BOGOTA = (4.7110, -74.0721)
BARRANQUILLA = (10.9685, -74.7813)


class TransporteFlask:
    """Enruta las llamadas HTTP del Gateway hacia el cliente de prueba del servicio real."""

    def __init__(self, cliente):
        self.cliente = cliente

    def solicitar(self, metodo, url, *, cabeceras=None, cuerpo=b"", tiempo_espera_ms=500):
        partes = urlsplit(url)
        ruta = partes.path + (("?" + partes.query) if partes.query else "")
        respuesta = self.cliente.open(path=ruta, method=metodo, headers=cabeceras or {}, data=cuerpo)
        return RespuestaHttpCruda(respuesta.status_code, dict(respuesta.headers), respuesta.data, 1.0)


class TransporteIdentidadParaDeteccion:
    """Permite al Detector consultar IValidarSesión del Identidad real en proceso."""

    def __init__(self, cliente):
        self.cliente = cliente

    def __call__(self, session_id):
        respuesta = self.cliente.post("/sesiones/validar", json={"session_id": session_id})
        return respuesta.status_code, respuesta.data


@pytest.fixture
def plataforma(tmp_path):
    """Levanta Identidad, Detector, Journey y el Gateway conectados entre sí."""
    identidad = crear_identidad(
        ConfiguracionIdentidad(SECRETO, url_base_datos=f"sqlite:///{tmp_path}/identidad.db")
    ).test_client()

    configuracion_deteccion = ConfiguracionDeteccion(
        url_base_datos=f"sqlite:///{tmp_path}/deteccion.db")
    repositorio = RepositorioClientes(AlmacenClientes(configuracion_deteccion.url_base_datos))
    bitacora = io.StringIO()

    detector = crear_deteccion(
        configuracion_deteccion,
        repositorio=repositorio,
        cliente_identidad=ClienteIdentidadDeteccion(
            "http://identity", transporte=TransporteIdentidadParaDeteccion(identidad)),
        auditoria=AuditoriaDeteccion(bitacora),
    ).test_client()

    journey = crear_journey().test_client()

    puerta = PolicyGate(
        ValidadorPyJwt(SECRETO),
        AnalizadorContextoSesion(),
        ClienteDetector(TransporteFlask(detector), "http://detector"),
        ClienteHttpJourney(TransporteFlask(journey), "http://journey"),
        ClienteIdentidad(TransporteFlask(identidad), "http://identity"),
    )

    gateway = crear_gateway(ConfiguracionPasarela(SECRETO), orquestador=puerta).test_client()

    return {
        "gateway": gateway, "identidad": identidad, "journey": journey,
        "repositorio": repositorio, "bitacora": bitacora,
    }


def _registrar_cliente(plataforma, session_id, device_id, ubicacion):
    """Da de alta la sesión en Identidad y su ubicación conocida en el Detector."""
    plataforma["identidad"].post("/sesiones", json={"session_id": session_id, "device_id": device_id})
    plataforma["repositorio"].registrar(
        session_id, "cliente-1", device_id, ubicacion[0], ubicacion[1],
        datetime.now(timezone.utc) - timedelta(minutes=5),
    )


def _pedir(plataforma, session_id, device_id, ubicacion, secreto=SECRETO):
    """Ejecuta una petición al journey a través del Gateway."""
    token = jwt.encode({"session_id": session_id, "device_id": device_id}, secreto, algorithm="HS256")
    contexto = json.dumps({
        "session_id": session_id, "device_id": device_id,
        "geo_lat": ubicacion[0], "geo_lon": ubicacion[1],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return plataforma["gateway"].get(
        "/api/journey/orders",
        headers={"Authorization": f"Bearer {token}", "X-Session-Context": contexto},
    )


def test_sesion_legitima_atraviesa_los_tres_servicios(plataforma):
    """Con dispositivo y ubicación registrados, la solicitud llega al journey."""
    _registrar_cliente(plataforma, "s-legitima", "dispositivo-1", BOGOTA)

    respuesta = _pedir(plataforma, "s-legitima", "dispositivo-1", BOGOTA)

    assert respuesta.status_code == 201
    assert respuesta.get_json() == {"journey": "ok"}
    assert plataforma["journey"].application.llamadas


def test_dispositivo_suplantado_se_detecta_y_suspende(plataforma):
    """Un dispositivo distinto es detectado por el Detector y suspendido por el Gateway."""
    _registrar_cliente(plataforma, "s-dispositivo", "dispositivo-1", BOGOTA)

    respuesta = _pedir(plataforma, "s-dispositivo", "dispositivo-robado", BOGOTA)

    assert respuesta.status_code == 403
    assert respuesta.get_json()["error_code"] == "verification_required"
    assert not plataforma["journey"].application.llamadas

    # La suspensión debe ser observable en la persistencia de Identidad.
    estado = plataforma["identidad"].post(
        "/sesiones/validar", json={"session_id": "s-dispositivo"}).get_json()
    assert estado["estado"] == "pendiente_verificacion"
    assert estado["sesion_activa"] is False


def test_ubicacion_incompatible_se_detecta_y_suspende(plataforma):
    """Una ubicación fuera del radio produce el mismo bloqueo, con motivo distinto."""
    _registrar_cliente(plataforma, "s-ubicacion", "dispositivo-1", BOGOTA)

    respuesta = _pedir(plataforma, "s-ubicacion", "dispositivo-1", BARRANQUILLA)

    assert respuesta.status_code == 403
    assert not plataforma["journey"].application.llamadas

    eventos = [json.loads(linea) for linea in plataforma["bitacora"].getvalue().splitlines()]
    evaluaciones = [evento for evento in eventos if evento["evento"] == "evaluacion"]
    assert evaluaciones[-1]["motivo"] == "ubicacion"


def test_el_contrato_del_detector_es_aceptado_por_el_gateway(plataforma):
    """El Gateway valida el veredicto de forma estricta; la integración lo comprueba de extremo a extremo."""
    _registrar_cliente(plataforma, "s-contrato", "dispositivo-1", BOGOTA)

    assert _pedir(plataforma, "s-contrato", "dispositivo-1", BOGOTA).status_code == 201

    eventos = [json.loads(linea) for linea in plataforma["bitacora"].getvalue().splitlines()]
    evaluacion = [evento for evento in eventos if evento["evento"] == "evaluacion"][-1]

    # El Detector recibió el request_id del Gateway sin rechazar la solicitud.
    assert evaluacion["request_id"]
    assert evaluacion["identidad_disponible"] is True
