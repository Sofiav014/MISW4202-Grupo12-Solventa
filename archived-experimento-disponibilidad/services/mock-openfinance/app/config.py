import os


_MODOS_PERMITIDOS = ("normal", "lento", "caido", "caida_temporal")


def _leer_modo() -> str:
    modo = os.getenv("MODO", "normal")
    if modo not in _MODOS_PERMITIDOS:
        modos_permitidos = ", ".join(_MODOS_PERMITIDOS)
        raise ValueError(f"MODO debe ser uno de: {modos_permitidos}")
    return modo


def _leer_auto_reparar_segundos(modo: str) -> int | None:
    if modo != "caida_temporal":
        return None

    valor = os.getenv("AUTO_REPARAR_SEGUNDOS")
    if valor is None or valor == "":
        raise ValueError(
            "AUTO_REPARAR_SEGUNDOS es obligatorio para caida_temporal"
        )

    try:
        segundos = int(valor)
    except ValueError as error:
        raise ValueError("AUTO_REPARAR_SEGUNDOS debe ser un entero") from error

    if segundos <= 0:
        raise ValueError("AUTO_REPARAR_SEGUNDOS debe ser mayor que cero")
    return segundos


MODO = _leer_modo()
AUTO_REPARAR_SEGUNDOS = _leer_auto_reparar_segundos(MODO)
PORT = 8002
