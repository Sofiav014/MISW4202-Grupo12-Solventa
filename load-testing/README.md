# Carga con Locust — Experimento HA2

Simula el tráfico de clientes (usuarios concurrentes golpeando `POST /cotizar`
en `journey`) y orquesta las condiciones de falla del proveedor (modo del
mock Open Finance) para reproducir los escenarios A–G del experimento. El
sistema bajo prueba (journey/adaptador/mock/Redis) es real; lo simulado es el
entorno que lo rodea: la carga entrante y el estado del proveedor.

## Estructura

```text
load-testing/
├── requirements.txt   # locust, redis, requests
├── locustfile.py       # el usuario simulado: POST /cotizar
├── control.py          # mock (POST /config), Redis, reinicio del adaptador
├── escenarios.py        # tabla de escenarios A-G (modo, carga, estado esperado)
└── run_escenario.py     # orquestador: reset -> preparación -> warm-up -> medición
```

## Preparar el ambiente

```bash
cd load-testing
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requiere además los servicios levantados (Docker Desktop corriendo):

```bash
cd ..
cp .env.example .env   # si no existe aún
docker-compose up -d
```

## Uso rápido: correr un escenario completo

`run_escenario.py` implementa el procedimiento de corrida controlada: recrea
el adaptador (breaker limpio y `ESCENARIO`/`EJECUCION_ID` correctos en sus
logs JSONL), vacía Redis y deja el mock en `normal`, corre un warm-up
descartable contra ese estado neutro, recién ahí establece las condiciones
propias del escenario (mock degradado, breaker forzado, cache miss) y corre
Locust headless con los parámetros de carga. Repite todo lo anterior tantas
veces como se pida (`--repeticiones`), para reproducibilidad — y `--escenario`
acepta una letra, una lista o `TODOS`, para correr varios o los siete sin
invocarlo uno por uno.

```bash
source .venv/bin/activate

# Escenario A (baseline), 3 repeticiones
python run_escenario.py --escenario A --repeticiones 3 --ejecucion-id run1

# Escenario B (lentitud), con carga custom
python run_escenario.py --escenario B -u 20 -r 5 -t 90s

# Escenario G (carga concurrente) combinado con proveedor degradado
python run_escenario.py --escenario G -u 100 -r 20 -t 2m --modo-proveedor lento

# Escenario F (cache miss) usando explícitamente el cliente sin caché
python run_escenario.py --escenario F --cliente-ids 99999

# Un subconjunto, en una sola corrida
python run_escenario.py --escenario A,B,C --repeticiones 3 --ejecucion-id suite1

# Los siete escenarios, 3 repeticiones cada uno (21 corridas), sin abortar
# la suite si alguna repetición individual falla por un error del harness
python run_escenario.py --escenario TODOS --repeticiones 3 --ejecucion-id suite1 --continuar-si-falla
```

Corriendo varios escenarios en una invocación, cada uno pasa igual por su
propio reset -> warm-up -> preparación -> medición — no se "arrastra" estado
de un escenario al siguiente. Al final se imprime un resumen (cuántas
corridas quedaron OK, cuáles fallaron y por qué) y el proceso termina con
código de salida 1 si hubo alguna falla del harness (no cuentan las fallas
HTTP esperadas de C/D/F, esas ya se miden con `--exit-code-on-error 0`).

Los resultados quedan en `resultados/locust/escenario_<X>/<ejecucion_id>_repN_stats.csv`
(y `_stats_history.csv`, `_failures.csv`). Para correlacionarlos con el
registro estructurado del adaptador (`resultados/adaptador.jsonl`), usa el
mismo `<ejecucion_id>_repN` como `ejecucion_id`.

### Flags relevantes

| Flag                       | Qué hace                                                           | Default                       |
| -------------------------- | ------------------------------------------------------------------ | ----------------------------- |
| `-e/--escenario`           | Letra (`A`), lista (`A,B,C`) o `TODOS`                             | — (obligatorio)               |
| `-u/--usuarios`            | Usuarios concurrentes (aplica a cada escenario elegido)            | según escenario (tabla abajo) |
| `-r/--spawn-rate`          | Usuarios nuevos por segundo al arrancar                            | según escenario               |
| `-t/--duracion`            | Duración de la corrida medida (`60s`, `2m`)                        | según escenario               |
| `--repeticiones`           | Cuántas veces repetir el procedimiento, por escenario              | `1`                           |
| `--modo-proveedor`         | Override del modo del mock (aplica en D/F/G)                       | según escenario               |
| `--cliente-ids`            | Override del pool de `cliente_id` usado en las requests            | según escenario               |
| `--sin-warmup`             | Salta el warm-up descartable                                       | (activado)                    |
| `--sin-reinicio-adaptador` | No recrea el adaptador (el breaker no queda limpio entre corridas) | (activado)                    |
| `--continuar-si-falla`     | En una suite de varios escenarios, sigue con el resto si uno falla | (activado)                    |

## Tabla de escenarios (`escenarios.py`)

| Escenario             | Modo Open Finance                        | Circuit Breaker                    | Redis                 | Carga por defecto        |
| --------------------- | ---------------------------------------- | ---------------------------------- | --------------------- | ------------------------ |
| A — Baseline          | NORMAL                                   | CLOSED                             | disponible            | 10 users, spawn 2, 60s   |
| B — Lentitud          | LENTO (900–1200ms > 700ms)               | CLOSED → OPEN                      | HIT (precargado)      | 10 users, spawn 2, 60s   |
| C — Caída total       | CAÍDO (100% error)                       | CLOSED → OPEN                      | HIT (precargado)      | 10 users, spawn 2, 60s   |
| D — Circuito abierto  | NORMAL (forzado tras abrir)              | OPEN (forzado antes de medir)      | HIT (precargado)      | 10 users, spawn 2, 60s   |
| E — Recuperación      | CAÍDO → NORMAL (a mitad de corrida)      | CLOSED → OPEN → HALF_OPEN → CLOSED | HIT (precargado)      | 10 users, spawn 2, 60s   |
| F — Cache miss        | CAÍDO (override con `--modo-proveedor`)  | OPEN (forzado antes de medir)      | MISS (nunca sembrado) | 10 users, spawn 2, 60s   |
| G — Carga concurrente | NORMAL (override con `--modo-proveedor`) | según degradación                  | HIT (precargado)      | 50 users, spawn 10, 120s |

Detalle de cómo se provoca cada condición está en `escenarios.py` (funciones
`_preparar_*`) y `control.py`.

## Correr Locust "a mano" (sin el orquestador)

Útil para pruebas rápidas o para pilotear con la UI web de Locust. El estado
del mock/Redis/breaker queda a tu cargo (usa las funciones de `control.py`
desde un `python -i` o `docker-compose` directamente).

```bash
# Headless, parámetros explícitos:
ESCENARIO=A CLIENTE_IDS=12345,67890 \
  locust -f locustfile.py --host http://localhost:8000 \
  --headless -u 20 -r 5 -t 60s --csv resultados/manual/run1

# Con la UI web (para explorar interactivamente):
locust -f locustfile.py --host http://localhost:8000
# abrir http://localhost:8089
```

Variables que lee `locustfile.py`:

| Variable                    | Uso                                                     | Default                 |
| --------------------------- | ------------------------------------------------------- | ----------------------- |
| `JOURNEY_URL`               | Host contra el que corre (si no se pasa `--host`)       | `http://localhost:8000` |
| `ESCENARIO`                 | Etiqueta el nombre de la request en las stats de Locust | `N/A`                   |
| `CLIENTE_IDS`               | Pool de `cliente_id` (uno al azar por request)          | `12345,67890`           |
| `WAIT_MIN_S` / `WAIT_MAX_S` | Think-time entre tareas de un mismo usuario simulado    | `0.1` / `0.5`           |

## Notas

- El Circuit Breaker (`pybreaker`) vive en memoria del proceso del adaptador:
  no hay endpoint para resetearlo, así que `control.reiniciar_adaptador()`
  recrea el contenedor (`docker compose up -d --force-recreate adaptador`)
  entre corridas. Por eso el reset es más lento que un simple restart, pero
  es lo que garantiza reproducibilidad y logs con el `ESCENARIO`/`EJECUCION_ID`
  correctos (esas variables solo se releen al recrear el contenedor).
- `control.py` lee `.env` en la raíz del repo (sin pisar variables ya
  definidas en el shell) para compartir `TTL_S`/`RESET_TIMEOUT_S` con
  docker-compose — así el escenario E espera el `RESET_TIMEOUT_S` real
  configurado, no un valor hardcodeado.
- El escenario E corre una `secuencia_especial` en un hilo aparte que espera
  `RESET_TIMEOUT_S + 1s` y recién ahí vuelve el mock a `normal`, para que la
  transición HALF_OPEN → CLOSED ocurra durante la corrida medida y quede
  reflejada en las stats de Locust y en el log del adaptador.
