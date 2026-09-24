import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from deteccion.aplicacion import crear_aplicacion
from deteccion.auditoria import RegistradorAuditoriaJson
from deteccion.cliente_identidad import ClienteIdentidad, DatosSesion, FalloIdentidad
from deteccion.configuracion import ConfiguracionDeteccion
from deteccion.persistencia import AlmacenClientes
from deteccion.repositorio import RepositorioClientes


BOGOTA = (4.7110, -74.0721)
# Barranquilla está a unos 700 km de Bogotá: muy por encima del radio por
# defecto, de modo que la prueba no depende del valor exacto del umbral.
BARRANQUILLA = (10.9685, -74.7813)


class IdentidadFalsa:
    """Doble de ms-identidad-sesiones con respuesta controlada por la prueba."""

    def __init__(self, device_id=None, fallo=None):
        self.device_id = device_id
        self.fallo = fallo
        self.consultas = []

    def validar_sesion(self, session_id):
        self.consultas.append(session_id)

        if self.fallo:
            return FalloIdentidad(self.fallo, 1.0)

        return DatosSesion(self.device_id, datetime.now(timezone.utc), True, "activa")


@pytest.fixture
def entorno(tmp_path):
    """Monta un Detector aislado con una base de datos temporal y una Identidad controlable."""
    configuracion = ConfiguracionDeteccion(url_base_datos=f"sqlite:///{tmp_path}/deteccion.db")
    repositorio = RepositorioClientes(AlmacenClientes(configuracion.url_base_datos))
    identidad = IdentidadFalsa(device_id="dispositivo-registrado")
    salida = io.StringIO()

    aplicacion = crear_aplicacion(
        configuracion,
        repositorio=repositorio,
        cliente_identidad=identidad,
        auditoria=RegistradorAuditoriaJson(salida),
    )

    return aplicacion.test_client(), repositorio, identidad, salida, configuracion


def _solicitud(device_id="dispositivo-registrado", ubicacion=BOGOTA, instante=None, **extra):
    """Construye el cuerpo que el Gateway envía a IDetecciónSesión."""
    cuerpo = {
        "session_id": "sesion-0001",
        "device_id": device_id,
        "geo_lat": ubicacion[0],
        "geo_lon": ubicacion[1],
        "timestamp": (instante or datetime.now(timezone.utc)).isoformat(),
    }
    cuerpo.update(extra)
    return cuerpo


def _sembrar(repositorio, ubicacion=BOGOTA, minutos_atras=5):
    """Registra la línea base del cliente usada por la mayoría de las pruebas."""
    repositorio.registrar(
        "sesion-0001",
        "cliente-0001",
        "dispositivo-registrado",
        ubicacion[0],
        ubicacion[1],
        datetime.now(timezone.utc) - timedelta(minutes=minutos_atras),
    )


# --- Criterio: dispositivo y ubicación coinciden -> normal ---

def test_sesion_legitima_produce_veredicto_normal(entorno):
    """Dispositivo y ubicación coincidentes con lo registrado dan veredicto normal."""
    cliente, repositorio, _, _, _ = entorno
    _sembrar(repositorio)

    respuesta = cliente.post("/evaluar", json=_solicitud())

    assert respuesta.status_code == 200
    assert respuesta.get_json() == {"veredicto": "normal", "motivo": None, "score_riesgo": 0.05}


# --- Criterio: device_id distinto -> sospechoso (motivo dispositivo) ---

def test_dispositivo_distinto_es_sospechoso_por_dispositivo(entorno):
    """Un dispositivo distinto del registrado se marca sospechoso con motivo dispositivo."""
    cliente, repositorio, _, _, _ = entorno
    _sembrar(repositorio)

    cuerpo = cliente.post("/evaluar", json=_solicitud(device_id="dispositivo-suplantado")).get_json()

    assert cuerpo["veredicto"] == "sospechoso"
    assert cuerpo["motivo"] == "dispositivo"
    assert cuerpo["score_riesgo"] > 0.9


# --- Criterio: ubicación fuera del radio -> sospechoso (motivo ubicación) ---

def test_ubicacion_fuera_del_radio_es_sospechosa_por_ubicacion(entorno):
    """Una ubicación más lejana que el radio permitido se marca con motivo ubicación."""
    cliente, repositorio, _, _, _ = entorno
    _sembrar(repositorio, ubicacion=BOGOTA)

    cuerpo = cliente.post("/evaluar", json=_solicitud(ubicacion=BARRANQUILLA)).get_json()

    assert cuerpo["veredicto"] == "sospechoso"
    assert cuerpo["motivo"] == "ubicacion"


def test_ubicacion_dentro_del_radio_no_dispara_la_regla(entorno):
    """Un desplazamiento pequeño respecto a la última ubicación sigue siendo normal."""
    cliente, repositorio, _, _, _ = entorno
    _sembrar(repositorio, ubicacion=BOGOTA)

    cercana = (BOGOTA[0] + 0.05, BOGOTA[1] + 0.05)

    assert cliente.post("/evaluar", json=_solicitud(ubicacion=cercana)).get_json()["veredicto"] == "normal"


def test_ubicacion_antigua_no_se_compara(entorno):
    """Fuera de la ventana de actividad reciente la ubicación previa deja de ser comparable."""
    cliente, repositorio, _, _, configuracion = entorno
    _sembrar(repositorio, ubicacion=BOGOTA, minutos_atras=configuracion.ventana_actividad_min + 30)

    cuerpo = cliente.post("/evaluar", json=_solicitud(ubicacion=BARRANQUILLA)).get_json()

    assert cuerpo["veredicto"] == "normal"


def test_solicitud_sospechosa_no_desplaza_la_linea_base(entorno):
    """Un suplantador no puede convertir su ubicación en la nueva referencia del cliente."""
    cliente, repositorio, _, _, _ = entorno
    _sembrar(repositorio, ubicacion=BOGOTA)

    cliente.post("/evaluar", json=_solicitud(ubicacion=BARRANQUILLA))
    historial = repositorio.obtener("sesion-0001")

    assert round(historial["ultima_lat"], 4) == round(BOGOTA[0], 4)

    # El segundo intento desde el mismo sitio sigue siendo sospechoso.
    assert cliente.post("/evaluar", json=_solicitud(ubicacion=BARRANQUILLA)).get_json()["veredicto"] == "sospechoso"


def test_solicitud_legitima_actualiza_la_linea_base(entorno):
    """Una solicitud normal sí refresca la última ubicación conocida."""
    cliente, repositorio, _, _, _ = entorno
    _sembrar(repositorio, ubicacion=BOGOTA)

    cercana = (BOGOTA[0] + 0.05, BOGOTA[1] + 0.05)
    cliente.post("/evaluar", json=_solicitud(ubicacion=cercana))

    assert round(repositorio.obtener("sesion-0001")["ultima_lat"], 4) == round(cercana[0], 4)


# --- Criterio: consume IValidarSesión, con manejo de error ---

def test_consulta_identidad_para_obtener_el_dispositivo_registrado(entorno):
    """El Detector consulta IValidarSesión en cada evaluación."""
    cliente, repositorio, identidad, _, _ = entorno
    _sembrar(repositorio)

    cliente.post("/evaluar", json=_solicitud())

    assert identidad.consultas == ["sesion-0001"]


def test_identidad_caida_degrada_al_registro_local(entorno):
    """Si Identidad no responde, la decisión se toma con el dispositivo registrado localmente."""
    cliente, repositorio, identidad, salida, _ = entorno
    _sembrar(repositorio)
    identidad.fallo = "tiempo_espera"

    cuerpo = cliente.post("/evaluar", json=_solicitud()).get_json()

    assert cuerpo["veredicto"] == "normal"

    eventos = [json.loads(linea) for linea in salida.getvalue().splitlines()]
    degradacion = [evento for evento in eventos if evento["evento"] == "identidad_degradada"]
    assert degradacion and degradacion[0]["categoria"] == "tiempo_espera"


def test_identidad_caida_sin_registro_local_resuelve_conservadoramente(entorno):
    """Sin Identidad y sin historial no hay línea base: la solicitud no puede considerarse legítima."""
    cliente, _, identidad, _, _ = entorno
    identidad.fallo = "conexion"

    cuerpo = cliente.post("/evaluar", json=_solicitud()).get_json()

    assert cuerpo["veredicto"] == "sospechoso"
    assert cuerpo["motivo"] == "dispositivo"


def test_sesion_desconocida_es_sospechosa(entorno):
    """Una sesión sin dispositivo registrado no tiene contra qué compararse."""
    cliente, _, identidad, _, _ = entorno
    identidad.device_id = None

    cuerpo = cliente.post("/evaluar", json=_solicitud()).get_json()

    assert cuerpo["veredicto"] == "sospechoso"
    assert cuerpo["motivo"] == "dispositivo"


# --- Contrato con el Gateway ---

def test_respuesta_tiene_exactamente_los_campos_del_contrato(entorno):
    """El Gateway valida el conjunto exacto de campos; cualquier extra invalidaría la respuesta."""
    cliente, repositorio, _, _, _ = entorno
    _sembrar(repositorio)

    cuerpo = cliente.post("/evaluar", json=_solicitud()).get_json()

    assert set(cuerpo) == {"veredicto", "motivo", "score_riesgo"}
    assert cuerpo["veredicto"] in {"normal", "sospechoso"}
    assert cuerpo["motivo"] in {None, "dispositivo", "ubicacion"}
    assert isinstance(cuerpo["score_riesgo"], float)


def test_acepta_los_campos_adicionales_que_envia_el_gateway(entorno):
    """El Gateway añade request_id al contrato; rechazarlo rompería la integración."""
    cliente, repositorio, _, _, _ = entorno
    _sembrar(repositorio)

    respuesta = cliente.post("/evaluar", json=_solicitud(request_id="abc-123"))

    assert respuesta.status_code == 200
    assert respuesta.get_json()["veredicto"] == "normal"


@pytest.mark.parametrize("cuerpo", [
    {},
    {"session_id": "s"},
    _solicitud(device_id=""),
    {**_solicitud(), "geo_lat": 91},
    {**_solicitud(), "geo_lon": "no-es-numero"},
    {**_solicitud(), "timestamp": "2026-01-01T00:00:00"},
])
def test_contexto_invalido_es_rechazado(entorno, cuerpo):
    """Un contexto que no cumple el contrato se rechaza antes de decidir."""
    cliente, _, _, _, _ = entorno

    assert cliente.post("/evaluar", json=cuerpo).status_code == 400


# --- Criterio: tiempo de respuesta dentro del sub-presupuesto ---

def test_la_evaluacion_registra_su_latencia_contra_el_presupuesto(entorno):
    """Cada evaluación deja registrada su latencia y si cumplió el sub-presupuesto."""
    cliente, repositorio, _, salida, configuracion = entorno
    _sembrar(repositorio)

    cliente.post("/evaluar", json=_solicitud())

    evaluacion = [
        json.loads(linea) for linea in salida.getvalue().splitlines()
        if json.loads(linea)["evento"] == "evaluacion"
    ][0]

    assert evaluacion["presupuesto_ms"] == configuracion.presupuesto_latencia_ms
    assert evaluacion["latencia_ms"] < configuracion.presupuesto_latencia_ms
    assert evaluacion["dentro_de_presupuesto"] is True


# --- Criterio: dataset semilla reproducible ---

def test_el_dataset_semilla_es_reproducible():
    """Dos generaciones con la misma semilla producen exactamente la misma población."""
    from deteccion.semilla import generar

    primera = generar(50, semilla=20260923)
    segunda = generar(50, semilla=20260923)

    assert len(primera) == 50
    assert [(c["session_id"], c["device_id"], c["lat"], c["lon"]) for c in primera] == \
           [(c["session_id"], c["device_id"], c["lat"], c["lon"]) for c in segunda]

    distinta = generar(50, semilla=999)
    assert [c["lat"] for c in distinta] != [c["lat"] for c in primera]


def test_la_carga_de_semilla_deja_clientes_evaluables(tmp_path):
    """Tras cargar la semilla, el Detector puede evaluar contra los clientes precargados."""
    from deteccion.semilla import cargar, generar

    configuracion = ConfiguracionDeteccion(url_base_datos=f"sqlite:///{tmp_path}/semilla.db")
    total = cargar(configuracion, cantidad=30, semilla=20260923)
    assert total == 30

    primero = generar(30, semilla=20260923)[0]
    repositorio = RepositorioClientes(AlmacenClientes(configuracion.url_base_datos))
    identidad = IdentidadFalsa(device_id=primero["device_id"])
    cliente = crear_aplicacion(
        configuracion, repositorio=repositorio, cliente_identidad=identidad,
        auditoria=RegistradorAuditoriaJson(io.StringIO()),
    ).test_client()

    legitima = {
        "session_id": primero["session_id"], "device_id": primero["device_id"],
        "geo_lat": primero["lat"], "geo_lon": primero["lon"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    assert cliente.post("/evaluar", json=legitima).get_json()["veredicto"] == "normal"

    suplantada = {**legitima, "device_id": "dispositivo-robado"}
    assert cliente.post("/evaluar", json=suplantada).get_json()["motivo"] == "dispositivo"


# --- Cliente de Identidad ---

def test_el_cliente_de_identidad_normaliza_los_fallos():
    """Una respuesta fuera de contrato se reporta como fallo, no como sesión válida."""
    def transporte_roto(_session_id):
        return 200, b"no-es-json"

    resultado = ClienteIdentidad("http://identidad", transporte=transporte_roto).validar_sesion("s")

    assert isinstance(resultado, FalloIdentidad)
    assert resultado.categoria == "contrato_invalido"


def test_el_cliente_de_identidad_interpreta_una_respuesta_valida():
    """La respuesta de IValidarSesión se convierte a los tipos internos del Detector."""
    def transporte(_session_id):
        return 200, json.dumps({
            "session_id": "s", "estado": "activa", "device_id_registrado": "d1",
            "ultima_actividad": "2026-09-23T12:00:00+00:00", "expira_en": None,
            "sesion_activa": True,
        }).encode()

    resultado = ClienteIdentidad("http://identidad", transporte=transporte).validar_sesion("s")

    assert isinstance(resultado, DatosSesion)
    assert resultado.device_id_registrado == "d1"
    assert resultado.sesion_activa is True
