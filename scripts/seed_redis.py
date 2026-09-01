#!/usr/bin/env python3
# Siembra Redis con perfiles de prueba — dueño: I4 (tarea 1.3)
# Uso: python scripts/seed_redis.py   (requiere: pip install redis)
import json
import redis

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

# Perfiles válidos (para escenarios con caché disponible)
perfiles = {
    "12345": {"cliente_id": "12345", "score_riesgo": 720, "fuente": "CACHE",
              "timestamp_perfil": "2026-08-31T10:00:00Z"},
    "67890": {"cliente_id": "67890", "score_riesgo": 650, "fuente": "CACHE",
              "timestamp_perfil": "2026-08-31T10:00:00Z"},
}

TTL_S = 300
for cid, perfil in perfiles.items():
    r.set(f"perfil:{cid}", json.dumps(perfil), ex=TTL_S)
    print(f"sembrado perfil:{cid} (TTL={TTL_S}s)")

# Nota: el cliente "99999" queda SIN perfil a propósito -> para probar cache miss (escenario F)
print("cliente 99999 dejado sin perfil (cache miss)")
