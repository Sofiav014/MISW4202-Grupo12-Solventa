import jwt
import pytest

from identidad.aplicacion import crear_aplicacion
from identidad.configuracion import ConfiguracionIdentidad


@pytest.fixture
def cliente(tmp_path):
    """Crea una aplicación de Identidad aislada, respaldada por SQLite en un directorio temporal."""
    configuracion = ConfiguracionIdentidad("secreto-test", url_base_datos=f"sqlite:///{tmp_path}/identidad.db")
    return crear_aplicacion(configuracion).test_client(), configuracion


def test_sesion_legitima_queda_activa_y_el_token_es_valido(cliente):
    """Una sesión recién creada queda activa y su JWT decodifica al session_id/device_id emitidos."""
    cliente_http, configuracion = cliente

    creacion = cliente_http.post("/sesiones", json={"session_id": "s1", "device_id": "d1"})
    assert creacion.status_code == 201
    cuerpo = creacion.get_json()
    assert cuerpo["estado"] == "activa"

    payload = jwt.decode(cuerpo["token"], configuracion.secreto_jwt, algorithms=[configuracion.algoritmo_jwt])
    assert payload["session_id"] == "s1"
    assert payload["device_id"] == "d1"

    consulta = cliente_http.post("/sesiones/validar", json={"session_id": "s1"}).get_json()
    assert consulta["sesion_activa"] is True
    assert consulta["device_id_registrado"] == "d1"
    assert consulta["ultima_actividad"] is not None


def test_sesion_expirada_se_reporta_inactiva(cliente):
    """Una sesión cuyo ttl ya venció se reclasifica como expirada al consultarla."""
    cliente_http, _ = cliente
    cliente_http.post("/sesiones", json={"session_id": "s2", "device_id": "d2", "ttl_segundos": -1})

    consulta = cliente_http.post("/sesiones/validar", json={"session_id": "s2"}).get_json()
    assert consulta["estado"] == "expirada"
    assert consulta["sesion_activa"] is False


def test_sesion_marcada_sospechosa_queda_pendiente_de_verificacion(cliente):
    """Iniciar verificación marca la sesión como pendiente y reutiliza la misma referencia en alertas repetidas."""
    cliente_http, _ = cliente
    cliente_http.post("/sesiones", json={"session_id": "s3", "device_id": "d3"})

    primera = cliente_http.post("/verificacion/iniciar", json={"session_id": "s3", "request_id": "r1"})
    assert primera.status_code == 200
    assert primera.get_json()["status"] == "verification_started"
    referencia = primera.get_json()["reference"]
    assert referencia

    # Una segunda alerta sobre la misma sesión no debe generar una referencia nueva.
    segunda = cliente_http.post("/verificacion/iniciar", json={"session_id": "s3", "request_id": "r2"})
    assert segunda.get_json() == {"status": "pending", "reference": referencia}

    consulta = cliente_http.post("/sesiones/validar", json={"session_id": "s3"}).get_json()
    assert consulta["estado"] == "pendiente_verificacion"
    assert consulta["sesion_activa"] is False


def test_verificacion_para_sesion_desconocida_la_crea_pendiente(cliente):
    """Iniciar verificación sobre un session_id sin registro previo lo crea directamente como pendiente."""
    cliente_http, _ = cliente
    respuesta = cliente_http.post("/verificacion/iniciar", json={"session_id": "nueva", "request_id": "r1"})
    assert respuesta.status_code == 200
    assert respuesta.get_json()["status"] == "verification_started"


def test_revocacion_tiene_efecto_inmediato_en_consultas_posteriores(cliente):
    """Revocar una sesión se refleja de inmediato en la siguiente consulta de validación."""
    cliente_http, _ = cliente
    cliente_http.post("/sesiones", json={"session_id": "s4", "device_id": "d4"})

    revocacion = cliente_http.post("/sesiones/s4/revocar")
    assert revocacion.status_code == 200
    assert revocacion.get_json()["estado"] == "revocada"

    consulta = cliente_http.post("/sesiones/validar", json={"session_id": "s4"}).get_json()
    assert consulta["estado"] == "revocada"
    assert consulta["sesion_activa"] is False


def test_revocar_sesion_inexistente_devuelve_404(cliente):
    """Revocar un session_id sin registro no crea estado nuevo; responde 404."""
    cliente_http, _ = cliente
    respuesta = cliente_http.post("/sesiones/no-existe/revocar")
    assert respuesta.status_code == 404


def test_token_invalido_es_rechazado(cliente):
    """Un token que no es un JWT válido se rechaza con 401 y valido=False."""
    cliente_http, _ = cliente
    respuesta = cliente_http.post("/jwt/validar", json={"token": "no-es-un-jwt"})
    assert respuesta.status_code == 401
    assert respuesta.get_json()["valido"] is False
