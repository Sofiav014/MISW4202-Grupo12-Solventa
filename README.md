# Experimento HA2 — Solventa (microservicios)

Esqueleto base de microservicios. Config centralizada en `.env`, un contenedor
por servicio, cada servicio autónomo en `services/`. I3 lo levanta primero y
desbloquea a todos; luego cada quien rellena **solo su servicio**.

## Estructura

```
solventa/
├── docker-compose.yml           # orquesta los 4 servicios, lee del .env
├── .env                         # parámetros de Fase 0 (NO se versiona)
├── .env.example                 # plantilla versionable
├── services/
│   ├── journey/                 # I3 — punto de entrada (POST /cotizar)
│   │   ├── app/{main,config}.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── adaptador/               # I2 (resiliencia) + I4 (caché)
│   │   └── app/{main,config}.py ...
│   └── mock-openfinance/        # I1 — proveedor simulado
│       └── app/{main,config}.py ...
└── scripts/
    └── seed_redis.py            # I4 — siembra perfiles (tarea 1.3)
```

Cada servicio tiene la misma estructura interna (`app/main.py` + `app/config.py`),
así que agregar uno nuevo = copiar una carpeta y sumarlo al compose.

## Levantar

```bash
cp .env.example .env        # primera vez
docker-compose up --build
```

Verificar salud:

```bash
curl http://localhost:8000/health                                   # journey
docker-compose exec adaptador python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8001/health').read())"
docker-compose exec redis redis-cli ping                            # PONG
```

Flujo completo:

```bash
curl -X POST http://localhost:8000/cotizar
# {"request_id":"...","prima":100,"fuente_perfil":"OPEN_FINANCE","resultado":"exitoso"}
```

Sembrar Redis (tras `docker-compose up`):

```bash
pip install redis && python scripts/seed_redis.py
```

## Quién rellena qué

| Servicio / script | Dueño | Qué agrega |
|---|---|---|
| `services/mock-openfinance/` | I1 | Modos SLOW/DOWN + `POST /config` |
| `services/adaptador/` | I2 | timeout + Circuit Breaker + estado en `/health` |
| `services/adaptador/` (caché) | I4 | fallback a Redis + write-back + cache miss |
| `services/journey/` | I3 | ya está; ajustar si cambia el contrato |
| `scripts/seed_redis.py` | I4 | perfiles de prueba con TTL |

## Config (buena práctica)

Todos los parámetros de Fase 0 viven en `.env` (una sola fuente de verdad).
`docker-compose` los inyecta como env vars; cada servicio los lee en su
`app/config.py`, nunca con `os.getenv` regado por el código. Cambiar una corrida
= editar `.env`. El estímulo (modo del proveedor) se cambia en caliente vía
`POST /config`. Nadie edita código Python para correr un escenario distinto.
```
```
