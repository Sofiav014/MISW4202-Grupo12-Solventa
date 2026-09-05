"""Pruebas sintéticas del pipeline 6.3; no son evidencia experimental."""

from copy import deepcopy
import hashlib
import unittest

import numpy as np
import pandas as pd

from analisis.procesamiento import recuperacion


def make_recovery_fixture() -> recuperacion.RecoveryInput:
    """Tres corridas sintéticas reproducibles con el contrato real; solo para asserts."""
    requests, events, manifests = [], [], []
    for repetition, (half, duration) in enumerate([(10.0, 0.1), (10.2, 0.2), (10.4, 0.3)], 1):
        run = f"STUB_E_rep{repetition}"
        origin = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=repetition)
        closed = half + duration
        def wall(seconds: float) -> str:
            return (origin + pd.Timedelta(seconds=seconds)).isoformat()
        rows = [(-1.1, -1, "CLOSED", "CLOSED", "PROVIDER"),
                (-0.01, 0.01, "CLOSED", "OPEN", "CACHE"),
                (2, 2.01, "OPEN", "OPEN", "CACHE"),
                (half - 0.01, closed + 0.005, "OPEN", "CLOSED", "PROVIDER"),
                (closed + 1, closed + 1.1, "CLOSED", "CLOSED", "PROVIDER"),
                (closed + 2, closed + 2.1, "CLOSED", "CLOSED", "PROVIDER")]
        for index, (start, end, before, after, source) in enumerate(rows):
            requests.append({"request_id": f"{run}-{index}", "ejecucion_id": run, "escenario": "E",
                             "ts_wall": wall(end), "timestamp_inicio": repetition * 1000 + start,
                             "timestamp_fin": repetition * 1000 + end, "estado_circuito_inicio": before,
                             "estado_circuito_fin": after, "fuente_respuesta": source,
                             "proveedor_invocado": before != "OPEN", "hit_miss": "HIT" if source == "CACHE" else "N/A",
                             "resultado": "degradado" if source == "CACHE" else "exitoso",
                             "logger": "adaptador.request", "event_type": "request"})
        for stamp, before, after in [(0, "closed", "open"), (half, "open", "half-open"), (closed, "half-open", "closed")]:
            events.append({"ts_wall": wall(stamp), "logger": "adaptador.circuit_breaker",
                           "estado_anterior": before, "estado_nuevo": after})
        manifests.append({"escenario": "E", "corrida_id": run,
                          "circuit_breaker": {"reset_timeout_seconds": 10, "fail_max": 1},
                          "cache": {"ttl_seconds": 300}, "mock_openfinance": {"modo": "caido"},
                          "carga": {"usuarios": 1, "duration_seconds": 20}})
    return recuperacion.RecoveryInput(requests, requests + events, manifests, "STUB / SINTÉTICOS")


def analyze(raw: recuperacion.RecoveryInput) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Ejercita la misma normalización y resumen que consume el notebook."""
    requests, transitions, conditions, _ = recuperacion.normalize_results(raw)
    return recuperacion.build_summary(requests, transitions, conditions)


class RecoveryPipelineTests(unittest.TestCase):
    """Conserva los casos de evidencia completa, ausente y contradictoria de 6.3."""

    def setUp(self) -> None:
        self.fixture = make_recovery_fixture()

    def test_three_complete_runs_and_recovery_times(self) -> None:
        summary, evidence, stats = analyze(self.fixture)
        self.assertEqual(len(summary), 3)
        self.assertTrue(summary.retorno_open_finance_confirmado.eq("Sí").all())
        self.assertTrue((summary.t_open < summary.t_half_open).all())
        self.assertTrue((summary.t_half_open < summary.t_closed).all())
        np.testing.assert_allclose(summary.tiempo_open_a_half_open, [10, 10.2, 10.4], atol=1e-6)
        np.testing.assert_allclose(summary.tiempo_half_open_a_closed, [0.1, 0.2, 0.3], atol=1e-6)
        np.testing.assert_allclose(summary.tiempo_recuperacion_total, [10.1, 10.4, 10.7], atol=1e-6)
        np.testing.assert_allclose(summary.delta_reset_timeout, [0, 0.2, 0.4], atol=1e-6)
        self.assertTrue(evidence.provider_posterior.eq(2).all())
        self.assertTrue(evidence.cache_posterior.eq(0).all())
        self.assertTrue(stats.valores_validos.eq(3).all())

    def test_missing_close_is_not_success(self) -> None:
        self.fixture.log = [r for r in self.fixture.log if not (
            r.get("logger") == "adaptador.circuit_breaker" and r.get("estado_nuevo") == "closed"
        )]
        summary, _, _ = analyze(self.fixture)
        self.assertTrue(summary.t_closed.isna().all())
        self.assertTrue(summary.tiempo_recuperacion_total.isna().all())
        self.assertTrue(summary.retorno_open_finance_confirmado.eq("No determinable").all())

    def test_missing_half_open_is_not_inferred(self) -> None:
        self.fixture.log = [r for r in self.fixture.log if r.get("estado_nuevo") != "half-open"]
        summary, _, _ = analyze(self.fixture)
        self.assertTrue(summary.t_half_open.isna().all())
        self.assertTrue(summary.retorno_open_finance_confirmado.eq("No determinable").all())

    def test_missing_timeout_does_not_invent_a_default(self) -> None:
        for manifest in self.fixture.manifests:
            manifest["circuit_breaker"].pop("reset_timeout_seconds")
        summary, _, _ = analyze(self.fixture)
        self.assertTrue(summary.reset_timeout.isna().all())
        self.assertTrue(summary.delta_reset_timeout.isna().all())
        self.assertTrue(summary.retorno_open_finance_confirmado.eq("Sí").all())

    def test_cache_after_closing_disproves_provider_recovery(self) -> None:
        for record in self.fixture.requests:
            if record["request_id"].endswith("-5"):
                record.update(fuente_respuesta="CACHE", resultado="degradado", proveedor_invocado=False)
        summary, evidence, _ = analyze(self.fixture)
        self.assertTrue(summary.retorno_open_finance_confirmado.eq("No").all())
        self.assertTrue(evidence.cache_posterior.eq(1).all())

    def test_absent_post_close_traffic_is_not_success(self) -> None:
        self.fixture.requests = [r for r in self.fixture.requests if not r["request_id"].endswith(("-4", "-5"))]
        self.fixture.log = self.fixture.requests + [r for r in self.fixture.log if r.get("logger") == "adaptador.circuit_breaker"]
        summary, _, _ = analyze(self.fixture)
        self.assertTrue(summary.retorno_open_finance_confirmado.eq("No determinable").all())

    def test_ambiguous_transitions_are_not_assigned(self) -> None:
        for record in deepcopy(self.fixture.requests):
            record.update(escenario="F", request_id="otro-" + record["request_id"])
            self.fixture.log.append(record)
        requests, transitions, conditions, issues = recuperacion.normalize_results(self.fixture)
        self.assertTrue(transitions.empty)
        self.assertTrue(any("ambigua" in issue["incidencia"] for issue in issues))
        summary, _, _ = recuperacion.build_summary(requests, transitions, conditions)
        self.assertTrue(summary.retorno_open_finance_confirmado.eq("No determinable").all())

    def test_csv_boolean_values(self) -> None:
        self.assertIs(recuperacion.parse_bool("false"), False)
        self.assertIs(recuperacion.parse_bool("true"), True)

    def test_hash_accepts_only_exact_or_crlf_equivalence(self) -> None:
        expected = hashlib.sha256(b"a\nb\n").hexdigest()
        self.assertEqual(recuperacion.check_hash(b"a\nb\n", expected, "fixture"), "exacto")
        self.assertIn("CRLF", recuperacion.check_hash(b"a\r\nb\r\n", expected, "fixture"))
        with self.assertRaises(ValueError):
            recuperacion.check_hash(b"contenido diferente", expected, "fixture")

    def test_invalid_timestamp_is_rejected(self) -> None:
        self.fixture.requests[0]["ts_wall"] = "no-es-un-timestamp"
        with self.assertRaises(ValueError):
            analyze(self.fixture)


if __name__ == "__main__":
    unittest.main()
