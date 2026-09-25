"""
Escenarios del experimento de seguridad (HA16).

Cada escenario fija una composición de tráfico distinta. A diferencia del
experimento de disponibilidad —donde los escenarios variaban el estado del
sistema— aquí lo que cambia es el tipo de sesión que llega, porque la
suplantación se detecta o no con una sola petición: el fenómeno no depende de
la carga.

El escenario D es el que puede refutar el diseño. Los demás confirman que cada
regla dispara cuando debe; D barre distancias alrededor del radio acordado y es
el único capaz de mostrar que el umbral elegido produce falsos positivos.
"""

from dataclasses import dataclass, field, replace

from parametros import RADIO_UBICACION_KM, TASA_TRAFICO_SUPLANTADO

from .poblacion import (
    MODO_DISPOSITIVO,
    MODO_FRONTERA,
    MODO_LEGITIMO,
    MODO_UBICACION,
)


# Desplazamiento de las suplantaciones por ubicación fuera de la frontera. Son
# cientos de kilómetros —el orden de magnitud entre dos ciudades del dataset—
# porque este escenario comprueba que la regla dispara ante un salto
# inequívoco, no que discrimine cerca del umbral: eso lo mide el escenario D.
DISTANCIA_SUPLANTACION_KM = 300.0

# Distancias de la barrida de frontera, en kilómetros. Se concentran alrededor
# de RADIO_UBICACION_KM (50) porque el interés está en si el sistema discrimina
# justo donde la regla decide, y se extienden a ambos lados para poder dibujar
# la curva de detección y ver dónde cruza realmente.
DISTANCIAS_FRONTERA = (30.0, 40.0, 45.0, 48.0, 49.0, 50.0, 51.0, 52.0, 55.0, 65.0, 80.0)


@dataclass(frozen=True)
class Escenario:
    """
    Composición de tráfico y parámetros de carga de un escenario.

    Es inmutable a propósito: el orquestador aplica los ajustes de la línea de
    comandos con `con_ajustes`, que devuelve una copia. Mutar el registro global
    haría que un ajuste de la primera repetición se filtrara a las siguientes.

    Attributes:
        letra: Identificador del escenario.
        nombre: Descripción corta para los informes.
        mezcla: Peso relativo de cada modo de construcción.
        usuarios: Usuarios concurrentes de Locust.
        spawn_rate: Usuarios nuevos por segundo.
        duracion: Duración de la corrida medida, en formato de Locust.
        distancias_frontera: Distancias a barrer, si el escenario usa frontera.
        mide: Qué responde este escenario, para documentar la evidencia.
    """

    letra: str
    nombre: str
    mezcla: dict[str, float]
    usuarios: int = 10
    spawn_rate: float = 10.0
    # Las corridas se mantienen cortas porque la línea base del Detector solo es
    # comparable dentro de la ventana de actividad reciente: una corrida larga
    # dejaría a los clientes sembrados fuera de esa ventana y la regla de
    # ubicación dejaría de aplicarse, produciendo falsos negativos que serían
    # un artefacto de la duración y no del sistema.
    duracion: str = "60s"
    distancias_frontera: tuple[float, ...] = ()
    mide: str = ""

    def con_ajustes(self, **ajustes) -> "Escenario":
        """Devuelve una copia con los campos indicados sustituidos."""
        return replace(self, **{k: v for k, v in ajustes.items() if v is not None})


ESCENARIOS: dict[str, Escenario] = {
    "A": Escenario(
        letra="A",
        nombre="Legítimo puro",
        mezcla={MODO_LEGITIMO: 1.0},
        mide=(
            "Tasa de falsos positivos sin ningún ataque presente. La tasa de "
            "detección queda indefinida aquí, no en cero: no hay suplantaciones "
            "que detectar."
        ),
    ),
    "B": Escenario(
        letra="B",
        nombre="Suplantación por dispositivo",
        mezcla={MODO_DISPOSITIVO: 1.0},
        mide="Detección de la regla de dispositivo con ubicación legítima.",
    ),
    "C": Escenario(
        letra="C",
        nombre="Suplantación por ubicación",
        mezcla={MODO_UBICACION: 1.0},
        mide=(
            "Detección de la regla de ubicación con el dispositivo registrado, "
            "ante un salto de cientos de kilómetros."
        ),
    ),
    "D": Escenario(
        letra="D",
        nombre="Frontera del radio",
        mezcla={MODO_FRONTERA: 1.0},
        distancias_frontera=DISTANCIAS_FRONTERA,
        mide=(
            f"Si el umbral de {RADIO_UBICACION_KM} km discrimina donde dice. "
            "Es el único escenario que puede refutar la regla de decisión."
        ),
    ),
    "E": Escenario(
        letra="E",
        nombre="Mezcla realista",
        mezcla={
            MODO_LEGITIMO: 1.0 - TASA_TRAFICO_SUPLANTADO,
            MODO_DISPOSITIVO: TASA_TRAFICO_SUPLANTADO / 2,
            MODO_UBICACION: TASA_TRAFICO_SUPLANTADO / 2,
        },
        mide=(
            "Latencia y clasificación con tráfico mixto, en la proporción "
            "acordada de suplantación."
        ),
    ),
}


def resolver(valor: str) -> list[str]:
    """
    Traduce el argumento de escenarios a una lista de letras válidas.

    Args:
        valor: Una letra, una lista separada por comas, o `TODOS`.

    Returns:
        Las letras solicitadas, ordenadas.

    Raises:
        SystemExit: Si alguna letra no corresponde a un escenario definido.
    """
    if valor.strip().upper() == "TODOS":
        return sorted(ESCENARIOS)

    letras = [letra.strip().upper() for letra in valor.split(",") if letra.strip()]
    invalidas = [letra for letra in letras if letra not in ESCENARIOS]

    if invalidas or not letras:
        raise SystemExit(
            f"Escenario(s) inválido(s): {', '.join(invalidas) or valor!r} — "
            f"válidos: {', '.join(sorted(ESCENARIOS))} o TODOS"
        )

    return letras


def descripcion(letra: str) -> str:
    """Devuelve una línea legible con el propósito de un escenario."""
    escenario = ESCENARIOS[letra]
    return f"{escenario.letra} — {escenario.nombre}: {escenario.mide}"
