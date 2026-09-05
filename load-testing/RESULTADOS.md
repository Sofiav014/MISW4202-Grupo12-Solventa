# Cómo leer los resultados

Cada corrida (`run_escenario.py`) deja rastro en dos lugares que hay que cruzar:

```text
resultados/
├── adaptador.jsonl                  # instrumentación por request, del lado del adaptador
└── escenario_<X>/
    └── <id>/
        ├── manifest.json                # condiciones efectivas de ESTA corrida
        ├── results_stats.csv            # resumen agregado
        ├── results_stats_history.csv    # ese mismo resumen, cada ~2s
        ├── results_failures.csv         # fallas agrupadas por mensaje
        └── results_exceptions.csv       # excepciones de Python en Locust (no HTTP)
```

`<id>` es `<ejecucion_id>_rep<N>` (ej. `run1_rep2`). El mismo `<id>` es el
`ejecucion_id` que vas a encontrar en las filas de `adaptador.jsonl` para esa
corrida — así se cruzan las tres fuentes (manifest, CSV, jsonl).

## `manifest.json` — condiciones de la corrida

Lo arma `experimentos.construir_manifest` (paquete en la raíz del repo) y
`run_escenario.py` lo guarda justo antes de medir. Es la respuesta a "¿con
qué parámetros corrió esto?" sin tener que ir a buscar `.env` o los flags de
la CLI:

```json
{
  "corrida_id": "run1_rep1",
  "escenario": "D",
  "timestamp": "2026-09-04T23:36:47Z",
  "circuit_breaker": { "fail_max": 1, "reset_timeout_seconds": 10 },
  "cache": { "ttl_seconds": 300 },
  "mock_openfinance": { "modo": "normal" },
  "carga": { "usuarios": 10, "duration_seconds": 6, "spawn_rate": 10.0 },
  "provider_timeout_ms": 700
}
```

Nunca se sobrescribe (se abre en modo exclusivo, falla si ya existe): es
evidencia de la corrida, no un archivo de estado. Si volvés a correr el mismo
`ejecucion_id`, el manifest original queda intacto (los CSV sí se
actualizan). Si necesitás
uno nuevo, usá otro `--ejecucion-id`.

## Verificación mínima antes de analizar

Al terminar cada corrida, `run_escenario.py` chequea que `results_stats.csv`
exista y tenga al menos una request en la fila `Aggregated` — si da 0, la
corrida no generó tráfico real (problema de conexión, escenario mal armado,
etc.) y se aborta con error en vez de dejar datos vacíos para analizar.

## Los CSV de Locust (por corrida)

### `_stats.csv` — el resumen que más se usa

Una fila por nombre de request (acá siempre `/cotizar[<ESCENARIO>]`) más una
fila `Aggregated`. Columnas:

| Columna                                | Qué es                                                                     |
| -------------------------------------- | -------------------------------------------------------------------------- |
| `Request Count` / `Failure Count`      | Total de requests y cuántas fallaron (ver más abajo qué cuenta como falla) |
| `Median/Average/Min/Max Response Time` | Latencia end-to-end vista por Locust, en **ms**                            |
| `Requests/s` / `Failures/s`            | Throughput promedio de toda la corrida                                     |
| `50%`...`100%`                         | Percentiles de latencia (ms)                                               |

Ejemplo real (escenario D, ya con la duración acotada):

```
Type,Name,Request Count,Failure Count,Median...,Average...,Min...,Max...,...
POST,/cotizar[D],174,0,7,8.3,3.3,23.2,...
```

**Qué cuenta como falla:** `locustfile.py` marca una request como fallida
solo si el status HTTP no es 200 (`respuesta.failure(...)` en el `catch_response`).
En C/D/F eso es _el resultado esperado del escenario_, no un error del
harness — por eso `run_escenario.py` corre Locust con `--exit-code-on-error 0`.

### `_stats_history.csv` — la serie de tiempo

Mismas columnas que `_stats.csv` más `Timestamp` (epoch) y `User Count`,
tomadas cada ~2s durante la corrida. Sirve para ver _cuándo_ ocurre una
transición (ej. cuándo sube la latencia en B/C al abrirse el breaker) en vez
de solo el promedio final.

### `_failures.csv`

Una fila por tipo de error, con cuántas veces ocurrió. En F, por ejemplo:

```
Method,Name,Error,Occurrences
POST,/cotizar[F],CatchResponseError('status=503 tipo_error=CACHE_MISS'),1859
```

### `_exceptions.csv`

Excepciones de Python dentro de Locust (bug en el locustfile, no una
respuesta HTTP). Vacío si todo corrió bien — si tiene filas, es un problema
del harness, no del escenario.

## `adaptador.jsonl`

Un log JSON por línea, compartido por **todas** las corridas (no se separa
por escenario ni se limpia entre corridas — filtrá por `ejecucion_id` y/o
`escenario`). Tiene 4 tipos de líneas, identificadas por `logger`:

- `werkzeug`: acceso HTTP crudo de Flask. Sin campos propios, se puede ignorar.
- `adaptador.cache`: una escritura o lectura de Redis (`cache_write`/`cache_fallback`).
- `adaptador.circuit_breaker`: una transición de estado del breaker.
- `adaptador.request`: **la fila importante** — una por cada `GET /perfil/<id>` que recibe el adaptador.

### `adaptador.request` — columnas

| Campo                              | Significado                                                                                                              |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `ejecucion_id`, `escenario`        | para cruzar con los CSV de Locust                                                                                        |
| `request_id`                       | id de la request (el mismo que generó journey)                                                                           |
| `timestamp_inicio`/`timestamp_fin` | reloj monotónico del proceso adaptador (no epoch); `latencia_total_ms` ya es la resta                                    |
| `estado_circuito_inicio`/`_fin`    | estado del breaker al entrar y al salir de la request                                                                    |
| `proveedor_invocado`               | si realmente se llamó a Open Finance (false si el circuito ya estaba OPEN al entrar)                                     |
| `fuente_respuesta`                 | `PROVIDER` (respondió el proveedor), `CACHE` (fallback a Redis), `NONE` (ni proveedor ni caché)                          |
| `hit_miss`                         | `HIT`/`EXPIRED`/`MISS` en Redis, o `N/A` si no se consultó caché                                                         |
| `latencia_proveedor_ms`            | cuánto tardó (o tardó en fallar) la llamada a Open Finance                                                               |
| `tiempo_conmutacion_ms`            | cuánto tardó el fallback a caché una vez detectada la falla — el "costo" de conmutar                                     |
| `latencia_total_ms`                | latencia end-to-end de la request, la que importa para el ASR                                                            |
| `resultado`                        | `exitoso` / `degradado` / `fallido`                                                                                      |
| `tipo_error`                       | motivo cuando no es `exitoso`: `PROVIDER_TIMEOUT`, `PROVIDER_UNAVAILABLE`, `CIRCUIT_OPEN`, `CACHE_MISS`, `CACHE_EXPIRED` |

### Cómo se combinan

| `resultado` | `fuente_respuesta` | `hit_miss`       | Qué pasó                                                                                    |
| ----------- | ------------------ | ---------------- | ------------------------------------------------------------------------------------------- |
| `exitoso`   | `PROVIDER`         | `N/A`            | camino feliz: proveedor respondió a tiempo                                                  |
| `degradado` | `CACHE`            | `HIT`            | proveedor caído/lento (o circuito ya OPEN) pero había caché fresca — el sistema se sostiene |
| `fallido`   | `NONE`             | `MISS`/`EXPIRED` | proveedor caído/lento **y** sin caché que servir — condición límite                         |

Ejemplo `degradado` (escenario B, el momento exacto en que se abre el breaker):

```json
{
  "escenario": "B",
  "estado_circuito_inicio": "CLOSED",
  "estado_circuito_fin": "OPEN",
  "fuente_respuesta": "CACHE",
  "hit_miss": "HIT",
  "latencia_proveedor_ms": 711.6,
  "tiempo_conmutacion_ms": 1.7,
  "latencia_total_ms": 713.5,
  "resultado": "degradado",
  "tipo_error": "CIRCUIT_OPEN"
}
```

Ejemplo `fallido` (escenario F, cache miss):

```json
{
  "escenario": "F",
  "fuente_respuesta": "NONE",
  "hit_miss": "MISS",
  "latencia_total_ms": 16.1,
  "resultado": "fallido",
  "tipo_error": "CACHE_MISS"
}
```

## Snippets rápidos (Python, sin dependencias)

Distribución de `resultado` por escenario:

```python
import json, collections
conteo = collections.Counter()
with open("resultados/adaptador.jsonl") as f:
    for l in f:
        d = json.loads(l)
        if d.get("event_type") == "request":
            conteo[(d["escenario"], d["resultado"])] += 1
for k, v in sorted(conteo.items()):
    print(k, v)
```

Filtrar una corrida puntual (`ejecucion_id`) y mirar la secuencia de estados del breaker:

```python
import json
with open("resultados/adaptador.jsonl") as f:
    reqs = [json.loads(l) for l in f if '"event_type": "request"' in l]
reqs = [r for r in reqs if r["ejecucion_id"] == "run1_rep1" and r["escenario"] == "E"]
prev = None
for r in reqs:
    par = (r["estado_circuito_inicio"], r["estado_circuito_fin"])
    if par != prev:
        print(r["timestamp_inicio"], par, r["fuente_respuesta"])
        prev = par
```

Leer un `_stats.csv` con `csv.DictReader` está en el propio `run_escenario.py`/README principal (sección de ejemplos de la suite).
