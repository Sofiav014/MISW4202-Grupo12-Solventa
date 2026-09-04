"""Definición de los escenarios A-G del experimento HA2.

Cada escenario fija el modo del mock Open Finance y el estado esperado de
caché/breaker al iniciar la corrida medida, además de valores por defecto de
carga (usuarios/spawn-rate/duración) — todos sobreescribibles por línea de
comandos en run_escenario.py.

`preparar` deja el sistema en las condiciones iniciales propias del
escenario, y corre DESPUÉS del warm-up (run_escenario.py se encarga de que
el warm-up golpee infraestructura ya reseteada pero todavía en modo normal).
Si `preparar` corriera antes del warm-up, el tráfico descartable del warm-up
sería el que dispara la apertura del breaker en B/C/E (FAIL_MAX=1 abre con
la primera falla), y la corrida medida arrancaría con el circuito ya abierto
en vez de mostrar la transición CLOSED->OPEN que el escenario busca medir.

`secuencia_especial`, cuando existe, corre en paralelo con la corrida medida
(solo el escenario E la necesita, para reparar el proveedor a mitad de
camino).
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


@dataclass
class Escenario:
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
    control.configurar_mock("normal")  # ya normal tras el reset; explícito por claridad


def _preparar_b(esc: "Escenario") -> None:
    control.calentar_cache(esc.cliente_ids)
    control.configurar_mock("lento")


def _preparar_c(esc: "Escenario") -> None:
    control.calentar_cache(esc.cliente_ids)
    control.configurar_mock("caido")


def _preparar_d(esc: "Escenario") -> None:
    control.calentar_cache(esc.cliente_ids)
    control.configurar_mock("caido")
    control.provocar_apertura_circuito(esc.cliente_ids[0])
    # El proveedor ya está sano; el breaker se queda OPEN hasta RESET_TIMEOUT_S.
    control.configurar_mock("normal")


def _preparar_e(esc: "Escenario") -> None:
    control.calentar_cache(esc.cliente_ids)
    control.configurar_mock("caido")
    control.provocar_apertura_circuito(esc.cliente_ids[0])
    # El mock vuelve a NORMAL a mitad de la corrida medida: ver _secuencia_e.


def _secuencia_e(esc: "Escenario") -> None:
    """Repara el proveedor bien antes de que expire RESET_TIMEOUT_S.

    El breaker se abrió durante `preparar` (justo antes de que arranque la
    corrida medida), así que su propio reloj de RESET_TIMEOUT_S ya viene
    corriendo. Si esta reparación tardara *más* que ese tiempo, el intento en
    HALF_OPEN caería sobre un proveedor todavía caído, fallaría, y el breaker
    reabriría reiniciando el conteo — perdiendo la recuperación dentro de la
    ventana de la corrida. Por eso reparamos temprano (30% del timeout, con
    piso de 1s) y dejamos el resto del tiempo como margen.
    """
    import time

    time.sleep(max(control.RESET_TIMEOUT_S * 0.3, 1))
    control.configurar_mock("normal")


def _preparar_f(esc: "Escenario") -> None:
    # cliente_ids de este escenario nunca se siembran; el reset ya los dejó en MISS.
    control.configurar_mock(esc.modo_proveedor)
    control.provocar_apertura_circuito(esc.cliente_ids[0])


def _preparar_g(esc: "Escenario") -> None:
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
        "D", "Circuito abierto", "normal", 10, 2, "60s", list(CLIENTES_CACHEADOS), _preparar_d,
        notas="Breaker forzado a OPEN antes de medir; caché ya poblada (HIT).",
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
