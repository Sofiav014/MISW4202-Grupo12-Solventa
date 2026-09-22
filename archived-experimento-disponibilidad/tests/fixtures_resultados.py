"""Fixtures/STUB de datos exclusivos de tests; no ejecutan ni simulan el experimento.

El manifest real lo produce 4.3. La instrumentacion existente emite las peticiones
en adaptador.jsonl; 5.4 puede separarlas por identidad. Se usa el contrato real.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from experimentos.resultados.contratos import COLUMNAS_ADJUNTOS, ValorJSON


def manifest_prueba(corrida_id: str = "prueba_rep1", escenario: str = "B") -> dict[str, ValorJSON]:
    """Snapshot determinista de test, nunca un default de corrida real."""
    return {
        "corrida_id": corrida_id, "escenario": escenario, "timestamp": "2026-09-04T20:30:00Z",
        "circuit_breaker": {"fail_max": 5, "reset_timeout_seconds": 30},
        "cache": {"ttl_seconds": 300}, "mock_openfinance": {"modo": "lento"},
        "carga": {"usuarios": 20, "duration_seconds": 60}, "provider_timeout_ms": 700,
    }


def registro_prueba(request_id: str = "peticion-test-1", corrida_id: str = "prueba_rep1", escenario: str = "B") -> dict[str, ValorJSON]:
    """Identidad minima de una peticion; no inventa observaciones ni metricas."""
    return {"request_id": request_id, "ejecucion_id": corrida_id, "escenario": escenario}


def escribir_entradas(raiz: Path, filas: list[dict[str, ValorJSON]] | None = None) -> tuple[Path, Path]:
    """Materializa fixtures en el directorio temporal que pertenece al test."""
    manifest, registros = raiz / "manifest.json", raiz / "requests.jsonl"
    manifest.write_text(json.dumps(manifest_prueba(), ensure_ascii=False) + "\n", encoding="utf-8")
    datos = [registro_prueba()] if filas is None else filas
    registros.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in datos), encoding="utf-8")
    return manifest, registros


def adjunto_prueba(nombre: str, escenario: str = "B", con_filas: bool = True) -> bytes:
    """CSV de fixture con estructura del productor; sus valores no son resultados reales."""
    columnas = COLUMNAS_ADJUNTOS[nombre]
    salida = io.StringIO(newline="")
    escritor = csv.writer(salida, lineterminator="\n")
    escritor.writerow(columnas)
    if con_filas:
        fila = {c: "0" for c in columnas}
        fila.update({"Name": f"/cotizar[{escenario}]", "Type": "POST", "Method": "POST"})
        escritor.writerow([fila[c] for c in columnas])
    return salida.getvalue().encode("utf-8")
