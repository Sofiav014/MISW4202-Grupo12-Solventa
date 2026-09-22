"""Gateway-only p95 evidence; this does not measure detector precision."""
import argparse
import json
import statistics
import time
import urllib.error
import uuid
import urllib.request

import jwt

def medir(base, secreto, escenario, cantidad, detector_base):
    total, detector_stub = [], []
    for _ in range(cantidad):
        token = jwt.encode({"sub": "measurement"}, secreto, algorithm="HS256")
        contexto = json.dumps({"session_id": str(uuid.uuid4()), "device_id": "measurement",
                               "geo_lat": 1, "geo_lon": 2, "timestamp": "2026-01-01T00:00:00+00:00"})
        headers = {"Authorization": f"Bearer {token}", "X-Session-Context": contexto}
        if escenario == "sospechoso":
            headers["X-Escenario-Stub"] = "sospechoso"
        detector_inicio = time.perf_counter()
        detector_solicitud = urllib.request.Request(detector_base + "/evaluar", data=json.dumps({
            "session_id": "measurement", "device_id": "measurement", "geo_lat": 1,
            "geo_lon": 2, "timestamp": "2026-01-01T00:00:00+00:00", "request_id": str(uuid.uuid4())
        }).encode(), headers={"Content-Type": "application/json", **({"X-Escenario-Stub": "sospechoso"} if escenario == "sospechoso" else {})})
        with urllib.request.urlopen(detector_solicitud, timeout=5) as respuesta:
            respuesta.read()
        detector_stub.append((time.perf_counter() - detector_inicio) * 1000)
        inicio = time.perf_counter()
        solicitud = urllib.request.Request(base + "/api/journey/measurement", headers=headers)
        try:
            with urllib.request.urlopen(solicitud, timeout=5) as respuesta:
                respuesta.read()
        except urllib.error.HTTPError as error:
            error.read()
        total.append((time.perf_counter() - inicio) * 1000)
    detector_p95 = statistics.quantiles(detector_stub, n=100)[94]
    return {"scenario": escenario, "requests": cantidad, "gateway_p95_ms": round(statistics.quantiles(total, n=100)[94], 2),
            "suspension_p95_ms": round(statistics.quantiles(total, n=100)[94], 2) if escenario == "sospechoso" else None,
            "detector_stub_observed_p95_ms": round(detector_p95, 2),
            "detector_budget_ms": 200,
            "note": "Detector p95 is a separate direct stub call, not gateway-internal timing; it includes local HTTP overhead and is not detector accuracy or integrated experiment evidence."}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--detector-url", default="http://localhost:8001")
    parser.add_argument("--secret", default="compose-development-only-change-me")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--scenario", choices=("normal", "sospechoso"), default="normal")
    args = parser.parse_args()
    if args.requests < 100:
        parser.error("--requests must be at least 100")
    print(json.dumps(medir(args.url, args.secret, args.scenario, args.requests, args.detector_url), sort_keys=True))
