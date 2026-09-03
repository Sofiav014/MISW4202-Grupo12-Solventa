import os

MOCK_URL = os.getenv("MOCK_URL", "http://mock-openfinance:8002")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# Parámetros de Fase 0 (leídos del .env vía docker-compose)
TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "700"))
FAIL_MAX = int(os.getenv("FAIL_MAX", "1"))
RESET_TIMEOUT_S = int(os.getenv("RESET_TIMEOUT_S", "10"))
HALF_OPEN_MAX = int(os.getenv("HALF_OPEN_MAX", "1"))
TTL_S = int(os.getenv("TTL_S", "300"))

# Instrumentación (Fase 4). Etiquetan cada corrida del experimento.
EJECUCION_ID = os.getenv("EJECUCION_ID", "local")
ESCENARIO = os.getenv("ESCENARIO", "N/A")
LOG_DIR = os.getenv("LOG_DIR")  # sin valor: solo stdout
LOG_PATH = f"{LOG_DIR}/adaptador.jsonl" if LOG_DIR else None

PORT = 8001
