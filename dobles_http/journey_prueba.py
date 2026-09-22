from flask import Flask, Response, request


def crear_aplicacion():
    aplicacion = Flask(__name__)
    llamadas = []

    @aplicacion.route("/<path:ruta>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def reenviar(ruta):
        llamadas.append({
            "metodo": request.method,
            "ruta": ruta,
            "consulta": request.query_string.decode(),
            "cuerpo": request.get_data(),
            "cabeceras": dict(request.headers),
        })
        escenario = request.headers.get("X-Escenario-Stub", "")
        if "journey-timeout" in escenario:
            return Response(status=504)
        if "journey-conexion" in escenario:
            return Response(status=502)
        if "journey-418" in escenario:
            return Response(b"teapot", status=418)
        return Response(b'{"journey":"ok"}', status=201, content_type="application/json",
                        headers={"ETag": '"normal"', "X-Internal": "must-not-forward"})

    aplicacion.llamadas = llamadas
    return aplicacion


app = crear_aplicacion()
