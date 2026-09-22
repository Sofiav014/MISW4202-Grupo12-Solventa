"""Utilidades para preparar el estado del sistema entre corridas.

Esta es la parte "no HTTP" del procedimiento de corrida controlada: cambiar
el modo del mock Open Finance, sembrar/vaciar Redis y reiniciar el adaptador
para limpiar el estado del Circuit Breaker (pybreaker vive en memoria del
proceso, así que solo un reinicio del contenedor lo resetea).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import redis
import requests

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_PROYECTO))  # para importar el paquete experimentos/

from experimentos import (  # noqa: E402
    ConfiguracionCarga,
    ConfiguracionMockOpenFinance,
    ManifestExistenteError,
    construir_manifest,
    guardar_manifest,
)


def _cargar_env_local() -> None:
    """Carga `.env` del repo sin sobrescribir variables ya definidas en el shell.

    Así este script comparte con docker-compose la misma fuente de verdad
    para TTL_S/RESET_TIMEOUT_S/FAIL_MAX/etc.
    """
    ruta_env = RAIZ_PROYECTO / ".env"
    if not ruta_env.exists():
        return
    for linea in ruta_env.read_text().splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.split("#", 1)[0].strip())


_cargar_env_local()

# URLs vistas desde el host (los puertos están publicados en docker-compose.yml),
# no las de red interna que usan los servicios entre sí.
JOURNEY_URL = os.getenv("JOURNEY_URL", "http://localhost:8000")
ADAPTADOR_URL = os.getenv("ADAPTADOR_URL", "http://localhost:8001")
MOCK_URL = os.getenv("MOCK_URL", "http://localhost:8002")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

TTL_S = int(os.getenv("TTL_S", "300"))
RESET_TIMEOUT_S = int(os.getenv("RESET_TIMEOUT_S", "10"))
FAIL_MAX = int(os.getenv("FAIL_MAX", "1"))
TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "700"))

_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def _timestamp_actual() -> str:
    """Devuelve la fecha/hora UTC actual en formato ISO 8601 con sufijo Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def configurar_mock(modo: str, timeout: float = 5.0) -> dict:
    """Cambia el modo del mock Open Finance (normal|lento|caido)."""
    resp = requests.post(f"{MOCK_URL}/config", json={"modo": modo}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def sembrar_perfil(cliente_id: str, score_riesgo: int = 720) -> None:
    """Precarga un perfil en Redis (para dejar el escenario en cache HIT)."""
    perfil = {
        "cliente_id": cliente_id,
        "score_riesgo": score_riesgo,
        "fuente": "CACHE",
        "timestamp_perfil": _timestamp_actual(),
    }
    _redis.set(f"perfil:{cliente_id}", json.dumps(perfil), ex=TTL_S)


def eliminar_perfil(cliente_id: str) -> None:
    """Quita la clave de Redis (para dejar el escenario en cache MISS)."""
    _redis.delete(f"perfil:{cliente_id}")


def flush_perfiles() -> None:
    """Vacía todos los perfiles cacheados, para no arrastrar estado entre corridas."""
    claves = _redis.keys("perfil:*")
    if claves:
        _redis.delete(*claves)


def calentar_cache(cliente_ids: Iterable[str]) -> None:
    """Precarga un perfil en Redis para cada cliente_id de la lista."""
    for cliente_id in cliente_ids:
        sembrar_perfil(cliente_id)


def estado_circuito(timeout: float = 5.0) -> str:
    """Consulta /health del adaptador y devuelve el estado del Circuit Breaker."""
    resp = requests.get(f"{ADAPTADOR_URL}/health", timeout=timeout)
    resp.raise_for_status()
    return resp.json()["circuito"]


def esperar_estado_circuito(
    esperado: str, timeout_s: float = 30.0, intervalo_s: float = 0.5
) -> str:
    """Sondea /health del adaptador hasta ver el estado esperado (CLOSED/OPEN/HALF_OPEN)."""
    fin = time.monotonic() + timeout_s
    ultimo = None
    while time.monotonic() < fin:
        try:
            ultimo = estado_circuito()
        except requests.RequestException:
            ultimo = None
        else:
            if ultimo == esperado:
                return ultimo
        time.sleep(intervalo_s)
    raise TimeoutError(
        f"circuito no llegó a {esperado} (quedó en {ultimo}) tras {timeout_s}s"
    )


def provocar_apertura_circuito(cliente_id: str, timeout_s: float = 30.0) -> None:
    """Fuerza al menos una falla contra el proveedor para abrir el breaker.

    Requiere que el mock ya esté en modo `caido` o `lento` (>700ms). Con
    FAIL_MAX=1 (default del experimento) alcanza con una sola solicitud.
    Pega directo al adaptador para no depender del journey.
    """
    try:
        requests.get(f"{ADAPTADOR_URL}/perfil/{cliente_id}", timeout=5)
    except requests.RequestException:
        pass
    esperar_estado_circuito("OPEN", timeout_s=timeout_s)


def _comando_compose() -> list[str]:
    """Devuelve el comando de Docker Compose disponible en el sistema."""
    try:
        subprocess.run(
            ["docker", "compose", "version"], check=True, capture_output=True
        )
        return ["docker", "compose"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ["docker-compose"]


def resetear_infraestructura(
    escenario: str = "N/A", ejecucion_id: str = "local", reiniciar: bool = True
) -> None:
    """Reset uniforme entre corridas: breaker limpio, Redis vacía, mock en
    `normal`. Deja el sistema en el mismo estado neutro sin importar qué
    escenario corrió antes ni cuál corre ahora — las condiciones propias del
    escenario las pone `Escenario.preparar` después del warm-up."""
    if reiniciar:
        reiniciar_adaptador(escenario=escenario, ejecucion_id=ejecucion_id)
    flush_perfiles()
    configurar_mock("normal")


def reiniciar_adaptador(
    escenario: str = "N/A", ejecucion_id: str = "local", timeout_s: float = 60.0
) -> None:
    """Recrea el contenedor del adaptador: breaker limpio + ESCENARIO/EJECUCION_ID
    correctos en sus logs JSONL (docker-compose solo relee esas env vars al
    recrear, `restart` no alcanza)."""
    env = os.environ.copy()
    env["ESCENARIO"] = escenario
    env["EJECUCION_ID"] = ejecucion_id
    subprocess.run(
        [*_comando_compose(), "up", "-d", "--force-recreate", "adaptador"],
        cwd=RAIZ_PROYECTO,
        check=True,
        capture_output=True,
        env=env,
    )
    esperar_estado_circuito("CLOSED", timeout_s=timeout_s)


@dataclass
class _ConfigParaManifest:
    """Satisface FuenteConfiguracionAdaptador sin importar services.adaptador."""

    EJECUCION_ID: str
    ESCENARIO: str
    FAIL_MAX: int
    RESET_TIMEOUT_S: int
    TTL_S: int
    TIMEOUT_MS: int
    LOG_DIR: str | None = None


def guardar_manifest_corrida(
    *,
    escenario: str,
    corrida_id: str,
    modo_mock: str,
    usuarios: int,
    duration_seconds: int,
    spawn_rate: float | None,
    resultados_dir: Path,
) -> Path | None:
    """Arma y persiste manifest.json con las condiciones efectivas de la corrida.

    Si ya existe (re-correr el mismo ejecucion_id/rep) no se sobrescribe: es evidencia.
    """
    manifest = construir_manifest(
        ConfiguracionMockOpenFinance(modo=modo_mock),
        ConfiguracionCarga(
            usuarios=usuarios, duration_seconds=duration_seconds, spawn_rate=spawn_rate
        ),
        fuente_configuracion=_ConfigParaManifest(
            EJECUCION_ID=corrida_id,
            ESCENARIO=escenario,
            FAIL_MAX=FAIL_MAX,
            RESET_TIMEOUT_S=RESET_TIMEOUT_S,
            TTL_S=TTL_S,
            TIMEOUT_MS=TIMEOUT_MS,
        ),
    )
    try:
        return guardar_manifest(manifest, directorio_resultados=resultados_dir)
    except ManifestExistenteError:
        return None
