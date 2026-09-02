# Microservicio mock-openfinance — dueño: I1 (Fase 2)
from flask import Flask, jsonify, request
from app.config import PORT
from app.generador_datos_financieros import GeneradorDatosFinancieros
from app.motor_comportamiento import (
    ConfiguracionComportamientoDTO,
    FallaSimuladaException,
    MotorComportamiento,
    TipoFallaEnum,
)

app = Flask(__name__)
generador_datos_financieros = GeneradorDatosFinancieros()
motor_comportamiento = MotorComportamiento()

CAMPOS_CONFIGURACION = {
    "latenciaMinMs",
    "latenciaMaxMs",
    "tasaError",
    "tipoFalla",
    "codigoHttpRespuesta",
}


def _serializar_configuracion(
    configuracion: ConfiguracionComportamientoDTO,
) -> dict:
    return {
        "latenciaMinMs": configuracion.latenciaMinMs,
        "latenciaMaxMs": configuracion.latenciaMaxMs,
        "tasaError": configuracion.tasaError,
        "tipoFalla": configuracion.tipoFalla.value,
        "codigoHttpRespuesta": configuracion.codigoHttpRespuesta,
    }


def _crear_configuracion(datos: object) -> ConfiguracionComportamientoDTO:
    if not isinstance(datos, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")

    campos_recibidos = set(datos)
    faltantes = CAMPOS_CONFIGURACION - campos_recibidos
    adicionales = campos_recibidos - CAMPOS_CONFIGURACION
    if faltantes:
        raise ValueError(f"faltan campos: {', '.join(sorted(faltantes))}")
    if adicionales:
        raise ValueError(f"campos no permitidos: {', '.join(sorted(adicionales))}")

    try:
        tipo_falla = TipoFallaEnum(datos["tipoFalla"])
    except (TypeError, ValueError) as error:
        raise ValueError("tipoFalla no es valido") from error

    return ConfiguracionComportamientoDTO(
        latenciaMinMs=datos["latenciaMinMs"],
        latenciaMaxMs=datos["latenciaMaxMs"],
        tasaError=datos["tasaError"],
        tipoFalla=tipo_falla,
        codigoHttpRespuesta=datos["codigoHttpRespuesta"],
    )


@app.get("/health")
def health():
    return jsonify(
        status="ok",
        service="mock-openfinance",
        configuracion=_serializar_configuracion(
            motor_comportamiento.configuracionActual
        ),
    )


@app.post("/config")
def actualizar_configuracion():
    try:
        nueva_configuracion = _crear_configuracion(request.get_json(silent=True))
        motor_comportamiento.actualizarConfiguracion(nueva_configuracion)
    except (TypeError, ValueError) as error:
        return (
            jsonify(error="CONFIGURACION_INVALIDA", detalle=str(error)),
            400,
        )

    return jsonify(_serializar_configuracion(nueva_configuracion))


@app.get("/perfil/<cliente_id>")
def perfil(cliente_id):
    motor_comportamiento.aplicarEfectosDeRed()
    motor_comportamiento.evaluarTasaError()
    return jsonify(generador_datos_financieros.obtenerPerfil(cliente_id))


@app.errorhandler(FallaSimuladaException)
def manejar_falla_simulada(error):
    if error.tipoFalla is TipoFallaEnum.HTTP_ERROR:
        codigo_respuesta = error.codigoHttpRespuesta
    else:
        codigo_respuesta = 503

    return (
        jsonify(
            error="FALLA_SIMULADA",
            tipoFalla=error.tipoFalla.value,
            codigoHttpRespuesta=codigo_respuesta,
        ),
        codigo_respuesta,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
