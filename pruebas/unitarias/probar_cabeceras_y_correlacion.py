import uuid

from gateway.clientes.journey import ClienteHttpJourney
from gateway.modelos import SolicitudProtegida


class Transporte:
    def solicitar(self, metodo, url, *, cabeceras, cuerpo, tiempo_espera_ms):
        self.cabeceras = cabeceras
        from gateway.clientes.transporte import RespuestaHttpCruda
        return RespuestaHttpCruda(200, {"Content-Type": "application/json"}, b"{}", 1)


def test_stub_header_is_stripped_by_default_and_request_id_is_authoritative():
    transporte = Transporte()
    cliente = ClienteHttpJourney(transporte, "http://journey")
    gateway_id = str(uuid.uuid4())
    solicitud = SolicitudProtegida("GET", "orders", "", {"Authorization": "Bearer x", "X-Escenario-Stub": "sospechoso", "X-Request-ID": "external"}, b"", gateway_id, "external")
    cliente.reenviar(solicitud)
    assert "X-Escenario-Stub" not in transporte.cabeceras
    assert transporte.cabeceras["request_id"] == gateway_id
    assert transporte.cabeceras["request_id"] != solicitud.id_correlacion_externo
    assert uuid.UUID(transporte.cabeceras["request_id"]).version == 4


def test_gateway_creates_uuid4_and_keeps_external_request_id_separate():
    import json
    import jwt
    from gateway.aplicacion import crear_aplicacion
    from gateway.configuracion import ConfiguracionPasarela
    from gateway.modelos import ErrorPasarela

    recibidas = []
    class Orquestador:
        def manejar(self, solicitud):
            recibidas.append(solicitud)
            return ErrorPasarela("auth_invalid", 401, "safe", solicitud.id_solicitud)

    app = crear_aplicacion(ConfiguracionPasarela("secret"), orquestador=Orquestador())
    token = jwt.encode({"sub": "u"}, "secret", algorithm="HS256")
    contexto = json.dumps({"session_id": "s", "device_id": "d", "geo_lat": 0, "geo_lon": 0, "timestamp": "2026-01-01T00:00:00+00:00"})
    app.test_client().get("/api/journey/orders", headers={"Authorization": f"Bearer {token}", "X-Session-Context": contexto, "X-Request-ID": "external"})
    assert uuid.UUID(recibidas[0].id_solicitud).version == 4
    assert recibidas[0].id_correlacion_externo == "external"
    assert recibidas[0].id_solicitud != recibidas[0].id_correlacion_externo
