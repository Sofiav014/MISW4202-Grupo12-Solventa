import os

MODO = os.getenv("MODO", "NORMAL")  # NORMAL | SLOW | DOWN
LATENCIA_NORMAL_MS = int(os.getenv("LATENCIA_NORMAL_MS", "200"))
LATENCIA_SLOW_MS = int(os.getenv("LATENCIA_SLOW_MS", "1500"))
PORT = 8002
