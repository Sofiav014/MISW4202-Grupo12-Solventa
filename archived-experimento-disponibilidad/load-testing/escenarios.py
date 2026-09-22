"""Definición de los escenarios A-G del experimento HA2.

Fija modo del mock, carga por defecto y estado esperado de caché/breaker por
escenario — todo sobreescribible por CLI en run_escenario.py. `preparar`
corre después del warm-up (si no, el warm-up sería el que abre el breaker en
B/C/E). `secuencia_especial` corre en paralelo a la corrida medida (solo E).
"""
from dataclasses import dataclass, field
from typing import Callable, Optional

import control

CLIENTES_CACHEADOS = ["12345", "67890"]
CLIENTE_SIN_CACHE = "99999"  # dejado sin sembrar a propósito (ver scripts/seed_redis.py)

# Cliente dedicado para el warm-up: si usara el pool del propio escenario,
# golpearía a CLIENTE_SIN_CACHE en modo normal y le sembraría caché antes de
# tiempo, arruinando la condición MISS que el escenario F necesita medir.
CLIENTE_WARMUP = "00000"

# D no mantiene al proveedor degradado, así que pybreaker se auto-cierra al
# expirar RESET_TIMEOUT_S; una corrida de 60s medía sobre todo tráfico ya
# recuperado. Se acota a una fracción del timeout para quedarse dentro de OPEN.
DURACION_D_S = max(round(control.RESET_TIMEOUT_S * 0.6), 3)

@dataclass
class Escenario:
    """Parámetros de carga y condiciones iniciales de un escenario A-G."""

    letra: str
    nombre: str
    modo_proveedor: str
    usuarios: int
    spawn_rate: float
    duracion: str
    cliente_ids: list
    preparar: Callable[["Escenario"], None]
    secuencia_especial: Optional[Callable[["Escenario"], None]] = None
    notas: str = ""


def _preparar_a(esc: "Escenario") -> None:
    """Deja el mock en modo normal: sin latencia ni errores artificiales."""
    control.configurar_mock("normal")


def _preparar_b(esc: "Escenario") -> None:
    """Precarga caché y pone el mock en modo lento (900-1200ms)."""
    control.calentar_cache(esc.cliente_ids)
    control.configurar_mock("lento")


def _preparar_c(esc: "Escenario") -> None:
    """Precarga caché y pone el mock en modo caído (100% de error)."""
    control.calentar_cache(esc.cliente_ids)
    control.configurar_mock("caido")


def _preparar_d(esc: "Escenario") -> None:
    """Precarga caché, fuerza el breaker a OPEN y deja el mock en normal.

    El proveedor queda sano pero el breaker sigue OPEN hasta que expire
    RESET_TIMEOUT_S (ver DURACION_D_S, que acota la corrida a ese margen).
    """
    control.calentar_cache(esc.cliente_ids)
    control.configurar_mock("caido")
    control.provocar_apertura_circuito(esc.cliente_ids[0])
    control.configurar_mock("normal")


def _preparar_e(esc: "Escenario") -> None:
    """Precarga caché y fuerza el breaker a OPEN con el mock caído.

    El mock vuelve a normal a mitad de la corrida medida (ver _secuencia_e).
    """
    control.calentar_cache(esc.cliente_ids)
    control.configurar_mock("caido")
    control.provocar_apertura_circuito(esc.cliente_ids[0])


def _secuencia_e(esc: "Escenario") -> None:
    """Repara el mock a normal antes de que expire RESET_TIMEOUT_S.

    Repara temprano (30% del timeout): si tardara más, el intento en
    HALF_OPEN caería sobre un proveedor aún caído y reabriría el breaker.
    """
    import time

    time.sleep(max(control.RESET_TIMEOUT_S * 0.3, 1))
    control.configurar_mock("normal")


def _preparar_f(esc: "Escenario") -> None:
    """Pone el mock degradado y fuerza el breaker a OPEN, sin caché (MISS).

    Los cliente_ids de este escenario nunca se siembran; el reset ya los
    dejó sin dato en Redis.
    """
    control.configurar_mock(esc.modo_proveedor)
    control.provocar_apertura_circuito(esc.cliente_ids[0])


def _preparar_g(esc: "Escenario") -> None:
    """Precarga caché y aplica el modo del proveedor configurado."""
    control.calentar_cache(esc.cliente_ids)
    control.configurar_mock(esc.modo_proveedor)


ESCENARIOS = {
    "A": Escenario(
        "A", "Baseline", "normal", 10, 2, "60s", list(CLIENTES_CACHEADOS), _preparar_a,
        notas="Sin latencia artificial, sin errores, circuito CLOSED, carga nominal.",
    ),
    "B": Escenario(
        "B", "Lentitud", "lento", 10, 2, "60s", list(CLIENTES_CACHEADOS), _preparar_b,
        notas="Latencia 900-1200ms > TIMEOUT_MS=700ms, tasa de error 0%; CLOSED->OPEN; cache HIT.",
    ),
    "C": Escenario(
        "C", "Caída total", "caido", 10, 2, "60s", list(CLIENTES_CACHEADOS), _preparar_c,
        notas="100% de error; CLOSED->OPEN; cache HIT.",
    ),
    "D": Escenario(
        "D", "Circuito abierto", "normal", 10, 10, f"{DURACION_D_S}s", list(CLIENTES_CACHEADOS), _preparar_d,
        notas=f"Breaker forzado a OPEN antes de medir; caché HIT. Duración acotada a {DURACION_D_S}s para no diluirse con tráfico ya recuperado.",
    ),
    "E": Escenario(
        "E", "Recuperación", "caido", 10, 2, "60s", list(CLIENTES_CACHEADOS), _preparar_e,
        secuencia_especial=_secuencia_e,
        notas="CAÍDO->NORMAL a mitad de la corrida; CLOSED->OPEN->HALF_OPEN->CLOSED.",
    ),
    "F": Escenario(
        "F", "Cache miss", "caido", 10, 2, "60s", [CLIENTE_SIN_CACHE], _preparar_f,
        notas="Clave nunca sembrada en Redis (MISS); proveedor degradado; circuito OPEN.",
    ),
    "G": Escenario(
        "G", "Carga concurrente", "normal", 50, 10, "120s", list(CLIENTES_CACHEADOS), _preparar_g,
        notas="Usuarios concurrentes altos; combinar con --modo-proveedor lento|caido.",
    ),
}
