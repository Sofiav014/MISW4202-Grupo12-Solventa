from flask import Flask, jsonify, request

def crear_aplicacion():
    aplicacion = Flask(__name__)
    llamadas = []
    @aplicacion.post("/verificacion/iniciar")
    def iniciar():
        llamadas.append(request.get_json())
        escenario = request.headers.get("X-Escenario-Stub", "confirmado")
        if "identidad-timeout" in escenario:
            return "timeout", 504
        if "identidad-conexion" in escenario:
            return "failure", 502
        if "identidad-json-invalido" in escenario:
            return "not-json", 200
        if "identidad-sin-referencia" in escenario:
            return jsonify(status="pending", reference=" ")
        if "identidad-status-invalido" in escenario:
            return jsonify(status="rejected", reference="ref")
        if "identidad-falla" in escenario:
            return jsonify(status="rejected", reference="ref-rechazada")
        return jsonify(status="pending" if escenario == "identidad-pendiente" else "verification_started", reference="ref-confirmada")
    aplicacion.llamadas = llamadas
    return aplicacion

app = crear_aplicacion()
