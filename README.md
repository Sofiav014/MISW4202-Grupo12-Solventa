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
