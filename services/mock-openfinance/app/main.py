# Microservicio mock-openfinance
from flask import Flask, jsonify, request
from app.config import AUTO_REPARAR_SEGUNDOS, MODO, PORT
from app.generador_datos_financieros import GeneradorDatosFinancieros
from app.motor_comportamiento import (
    ConfiguracionComportamientoDTO,
    FabricaModosOpenFinance,
    MotorComportamiento,
)


def _crear_configuracion_inicial(
    modo: str,
    auto_reparar_segundos: int | None,
) -> ConfiguracionComportamientoDTO:
    if modo == "caida_temporal":
        if auto_reparar_segundos is None:
            raise ValueError(
                "auto_reparar_segundos es obligatorio para caida_temporal"
            )
        return FabricaModosOpenFinance.modo_caida_temporal(
            auto_reparar_segundos
        )

    fabricas = {
        "normal": FabricaModosOpenFinance.modo_normal,
        "lento": FabricaModosOpenFinance.modo_lento,
        "caido": FabricaModosOpenFinance.modo_caido,
    }
    return fabricas[modo]()


app = Flask(__name__)
generador_datos_financieros = GeneradorDatosFinancieros()
motor_comportamiento = MotorComportamiento(
    _crear_configuracion_inicial(MODO, AUTO_REPARAR_SEGUNDOS)
)


def _serializar_configuracion(
    configuracion: ConfiguracionComportamientoDTO,
) -> dict:
    return {
        "latenciaMinMs": configuracion.latenciaMinMs,
        "latenciaMaxMs": configuracion.latenciaMaxMs,
        "tasaError": configuracion.tasaError,
        "autoRepararSegundos": configuracion.autoRepararSegundos,
    }


def _crear_configuracion(datos: object) -> ConfiguracionComportamientoDTO:
    if not isinstance(datos, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")

    if "modo" not in datos:
        raise ValueError("falta el campo: modo")

    modo = datos["modo"]
    fabricas = {
        "normal": FabricaModosOpenFinance.modo_normal,
        "lento": FabricaModosOpenFinance.modo_lento,
        "caido": FabricaModosOpenFinance.modo_caido,
    }
    if modo == "caido_temporal":
        campos_permitidos = {"modo", "autoRepararSegundos"}
    elif modo in fabricas:
        campos_permitidos = {"modo"}
    else:
        raise ValueError("modo no es valido")

    campos_recibidos = set(datos)
    faltantes = campos_permitidos - campos_recibidos
    adicionales = campos_recibidos - campos_permitidos
    if faltantes:
        raise ValueError(f"faltan campos: {', '.join(sorted(faltantes))}")
    if adicionales:
        raise ValueError(f"campos no permitidos: {', '.join(sorted(adicionales))}")

    if modo == "caido_temporal":
        return FabricaModosOpenFinance.modo_caida_temporal(
            datos["autoRepararSegundos"]
        )
    return fabricas[modo]()


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
        configuracion_actualizada = motor_comportamiento.actualizarConfiguracion(
            nueva_configuracion
        )
    except (TypeError, ValueError) as error:
        return (
            jsonify(error="CONFIGURACION_INVALIDA", detalle=str(error)),
            400,
        )

    if not configuracion_actualizada:
        return jsonify(error="MODO_TEMPORAL_ACTIVO"), 409

    return jsonify(_serializar_configuracion(nueva_configuracion))


@app.get("/perfil/<cliente_id>")
def perfil(cliente_id):
    motor_comportamiento.aplicarEfectosDeRed()
    if motor_comportamiento.evaluarTasaError():
        return jsonify(error="FALLA_SIMULADA"), 503
    return jsonify(generador_datos_financieros.obtenerPerfil(cliente_id))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
