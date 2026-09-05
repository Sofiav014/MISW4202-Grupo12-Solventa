"""Locustfile del experimento HA2.

Simula el tráfico de clientes golpeando el journey (POST /cotizar). El
estado del proveedor/breaker/caché se prepara aparte con run_escenario.py;
este archivo únicamente genera carga.
"""
import os
import random

from locust import HttpUser, between, task

ESCENARIO = os.getenv("ESCENARIO", "N/A")
CLIENTE_IDS = [
    c.strip() for c in os.getenv("CLIENTE_IDS", "12345,67890").split(",") if c.strip()
]
WAIT_MIN_S = float(os.getenv("WAIT_MIN_S", "0.1"))
WAIT_MAX_S = float(os.getenv("WAIT_MAX_S", "0.5"))


class UsuarioCotizacion(HttpUser):
    """Simula un usuario final pidiendo una cotización end-to-end."""

    host = os.getenv("JOURNEY_URL", "http://localhost:8000")
    wait_time = between(WAIT_MIN_S, WAIT_MAX_S)

    @task
    def cotizar(self):
        """Pide una cotización a un cliente al azar del pool y registra el resultado."""
        cliente_id = random.choice(CLIENTE_IDS)
        with self.client.post(
            "/cotizar",
            json={"cliente_id": cliente_id},
            name=f"/cotizar[{ESCENARIO}]",
            catch_response=True,
        ) as respuesta:
            if respuesta.status_code != 200:
                detalle = respuesta.json() if respuesta.text else {}
                respuesta.failure(
                    f"status={respuesta.status_code} "
                    f"tipo_error={detalle.get('tipo_error')}"
                )
