import json
from datetime import datetime

from gateway.validacion import AnalizadorContextoSesion


def cabecera(**cambios):
    datos = {"session_id": "s1", "device_id": "d1", "geo_lat": 1, "geo_lon": 2, "timestamp": "2026-01-01T00:00:00+00:00"}
    datos.update(cambios)
    return {"X-Session-Context": json.dumps(datos)}


def test_contexto_valido_se_normaliza():
    resultado = AnalizadorContextoSesion().analizar(cabecera())
    assert resultado.id_sesion == "s1"
    assert isinstance(resultado.instante, datetime)


def test_contexto_invalido_rechaza_rango_y_vacios():
    analizador = AnalizadorContextoSesion()
    assert analizador.analizar(cabecera(geo_lat=91)).codigo == "invalid_session_context"
    assert analizador.analizar(cabecera(device_id="")).codigo == "invalid_session_context"
