# Experimento HA: Solventa

Implementación base del experimento **HA2**, utilizando microservicios independientes y Docker Compose.

La configuración compartida se gestiona mediante el archivo `.env`, mientras que cada servicio mantiene su implementación dentro del directorio `services/`.

## Estructura del proyecto

```text
solventa/
├── docker-compose.yml
├── .env
├── .env.example
├── services/
│   ├── journey/
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   └── config.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── adaptador/
│   │   └── app/
│   │       ├── main.py
│   │       └── config.py
│   └── mock-openfinance/
│       └── app/
│           ├── main.py
│           └── config.py
└── scripts/
    └── seed_redis.py
```

Los servicios mantienen una estructura similar, utilizando `app/main.py` como punto de entrada y `app/config.py` para la configuración.

## Ejecución

Crear el archivo de configuración local a partir del ejemplo:

```bash
cp .env.example .env
```

Levantar los servicios:

```bash
docker-compose up --build
```

## Verificación de servicios

### Journey

```bash
curl http://localhost:8000/health
```

### Adaptador

```bash
docker-compose exec adaptador python -c \
"import urllib.request; print(urllib.request.urlopen('http://localhost:8001/health').read())"
```

### Redis

```bash
docker-compose exec redis redis-cli ping
```

Respuesta esperada:

```text
PONG
```

## Probar el flujo de cotización

```bash
curl -X POST http://localhost:8000/cotizar
```

Ejemplo de respuesta:

```json
{
  "request_id": "...",
  "prima": 100,
  "fuente_perfil": "OPEN_FINANCE",
  "resultado": "exitoso"
}
```

## Datos de prueba en Redis

Con los servicios levantados:

```bash
pip install redis
python scripts/seed_redis.py
```

El script carga los perfiles necesarios para ejecutar los escenarios de prueba asociados al uso de caché.

## Distribución de componentes

| Componente                   | Responsable | Alcance                                                                                               |
| ---------------------------- | ----------- | ----------------------------------------------------------------------------------------------------- |
| `services/mock-openfinance/` | I1          | Simulación del proveedor Open Finance, incluyendo modos `lento`, `caido` y `caida_temporal`, y el endpoint `POST /config`. |
| `services/adaptador/`        | I2          | Timeout, Circuit Breaker y exposición de su estado mediante `/health`.                                |
| `services/journey/`          | I3          | Implementación del flujo principal mediante el endpoint `POST /cotizar`.                              |
| `services/adaptador/`        | I4          | Fallback a Redis, actualización de caché y manejo de escenarios de cache miss.                        |
| `scripts/seed_redis.py`      | I4          | Carga de perfiles de prueba en Redis con TTL.                                                         |

## Configuración

Los parámetros utilizados por el experimento se definen en `.env` y son inyectados en los contenedores mediante Docker Compose.

Cada servicio centraliza la lectura de estas variables en su archivo `app/config.py`.

El proveedor simulado acepta `MODO=normal|lento|caido|caida_temporal`. Cuando
se utiliza `caida_temporal`, también se debe definir
`AUTO_REPARAR_SEGUNDOS` con un entero mayor que cero.

Los escenarios simulados del proveedor Open Finance pueden modificarse durante la ejecución mediante:

```text
POST /config
```

Esto permite cambiar el comportamiento del proveedor y ejecutar los distintos escenarios del experimento sin modificar el código de los servicios.

## Evidencia de ejecución por corrida (4.3)

El paquete `experimentos` construye y persiste un `manifest.json` con las
condiciones efectivas de una corrida. El manifest registra metadatos; no cambia
el modo del Mock, no administra Redis o el Circuit Breaker y no lanza Locust.

La configuración del adaptador (`EJECUCION_ID`, `ESCENARIO`, `FAIL_MAX`,
`RESET_TIMEOUT_S`, `TTL_S`, `TIMEOUT_MS` y `LOG_DIR`) se lee de
`services.adaptador.app.config`. El modo vigente del Mock y la configuración de
carga se reciben explícitamente para evitar registrar valores ficticios o un
modo de arranque que ya haya cambiado mediante `POST /config`.

La persistencia utiliza esta estructura y nunca sobrescribe un manifest:

```text
resultados/
└── escenario_<A-G>/
    └── <corrida_id>/
        ├── manifest.json
        └── results.csv  # producido por 5.4; 4.3 no lo crea ni analiza
```

Ejemplo programático, usando valores reales recibidos por el futuro
orquestador de la corrida:

```python
from pathlib import Path

from experimentos import (
    ConfiguracionCarga,
    ConfiguracionMockOpenFinance,
    construir_manifest,
    guardar_manifest,
)


def registrar_condiciones_corrida(
    modo_mock: str,
    usuarios: int,
    duration_seconds: int,
    spawn_rate: float | None,
    auto_repair_seconds: int | None = None,
) -> Path:
    manifest = construir_manifest(
        ConfiguracionMockOpenFinance(
            modo=modo_mock,
            auto_repair_seconds=auto_repair_seconds,
        ),
        ConfiguracionCarga(
            usuarios=usuarios,
            duration_seconds=duration_seconds,
            spawn_rate=spawn_rate,
        ),
    )
    return guardar_manifest(manifest)
```

### Fronteras pendientes

**Campo:** modo vigente del Mock y `auto_repair_seconds` cuando aplique.

**Estado actual:** contrato tipado obligatorio; 4.3 no consulta ni configura el Mock.

**Proveedor futuro:** Mock OpenFinance/I1 u orquestador de escenarios.

**Contrato esperado:** `ConfiguracionMockOpenFinance` con uno de
`normal | lento | caido | caida_temporal` y autorreparación positiva para el
modo temporal.

**Campo:** `usuarios`, `duration_seconds` y, si está disponible, `spawn_rate`.

**Estado actual:** contrato tipado sin valores predeterminados de ejecución.

**Proveedor futuro:** Locust/I4 u orquestador de carga.

**Contrato esperado:** `ConfiguracionCarga` con usuarios y duración positivos;
`spawn_rate` positivo o ausente.

**Campo:** significado operativo de los escenarios A–G.

**Estado actual:** el repositorio solo dispone de la etiqueta `ESCENARIO`.

**Proveedor futuro:** protocolo experimental.

**Contrato esperado:** entregar una etiqueta entre `A` y `G`; 4.3 la registra
sin ejecutar ni interpretar el escenario.

## Guardar resultados de una corrida (5.4)

La operación `guardar-resultados` recibe los manifests de 4.3 y separa las
peticiones de `adaptador.jsonl` por escenario y ejecución. Completa cada carpeta
con `results.csv`, procedencia y constancia de integridad, conservando los CSV
agregados existentes. Funciona sin Locust ni servicios levantados y no exige
que otro productor exporte un archivo de peticiones por corrida.

```bash
python load-testing/run_escenario.py guardar-resultados --manifests-dir resultados --log-compartido resultados/adaptador.jsonl --resultados-dir resultados
```

Contratos, adjuntos opcionales, API, límites y pruebas:
[Actividad 5.4 — Guardar resultados](docs/actividad_5_4.md).
