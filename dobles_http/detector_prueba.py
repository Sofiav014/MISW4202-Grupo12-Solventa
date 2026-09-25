from flask import Flask, jsonify, request


def crear_aplicacion():
    aplicacion = Flask(__name__)
    llamadas = []

    @aplicacion.post("/evaluar")
    def evaluar():
        datos = request.get_json()
        llamadas.append(datos)
        escenario = request.headers.get("X-Escenario-Stub", "")
        if "timeout" in escenario:
            from flask import abort
            abort(504)
        if "conexion" in escenario:
            abort(502)
        if "http-500" in escenario:
            return "failure", 500
        if "json-invalido" in escenario:
            return "not-json", 200, {"Content-Type": "text/plain"}
        if "veredicto-invalido" in escenario:
            return jsonify(veredicto="desconocido", motivo=None, score_riesgo=.5)
        if "objeto-invalido" in escenario:
            return jsonify(["invalid"])
        if "score-infinito" in escenario:
            return jsonify(veredicto="normal", motivo=None, score_riesgo=float("inf"))
        if "sospechoso" in escenario or "identidad-falla" in escenario:
            return jsonify(veredicto="sospechoso", motivo="ubicacion", score_riesgo=0.91)
        return jsonify(veredicto="normal", motivo=None, score_riesgo=0.12)

    aplicacion.llamadas = llamadas
    return aplicacion


app = crear_aplicacion()
