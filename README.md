# Solventa Gateway

Experimento de arquitectura del caso Solventa para la historia HA16: detectar una suplantación de identidad y suspender la operación antes de que alcance el servicio protegido, en menos de un segundo.

El gateway autentica y valida cada solicitud, consulta al Detector, inicia la verificación de Identidad para las sesiones sospechosas y solo redirige al Journey las normales.

## Instalación limpia

Instala el proyecto y sus herramientas de pruebas con `python -m pip install -e ".[test]"` y luego ejecuta `python -m pytest`.

En un entorno limpio, puedes utilizar `python -m venv .venv`, seguido de `.venv/bin/python -m pip install -e ".[test]"` y `.venv/bin/python -m pytest` (en Windows, utiliza `.venv\Scripts\python.exe`).

## Ejecución rápida con Compose

Copia `.env.example` como `.env` y luego ejecuta `docker compose config` y `docker compose up --build`.

Levanta los cuatro servicios con el Detector y el servicio de Identidad reales. El gateway queda en http://localhost:8080, y el Detector (8001), Identidad (8002) y el journey (8003) se publican para poder sembrarlos y consultarlos desde el generador de tráfico, que corre fuera de los contenedores.

Una solicitud válida debe incluir `Authorization` con un JWT HS256 y `X-Session-Context` con exactamente cinco campos (`session_id`, `device_id`, `geo_lat`, `geo_lon`, `timestamp`, este último con zona horaria explícita). Una sesión normal devuelve la respuesta del journey; una sospechosa devuelve `403 verification_required` tras invocar Identidad, sin llegar nunca al journey.

`PERMITIR_ESCENARIO_STUB` queda en `false`: encendida, la cabecera `X-Escenario-Stub` permitiría que el propio cliente eligiera el veredicto, y una corrida mediría lo que el generador pidió en vez de lo que el sistema decidió.

## Servicio de Identidad (ms-identidad-sesiones)

`ms-identidad-sesiones` (paquete `identidad/`) es la implementación real del componente de Identidad. Persiste sesiones en SQLite vía SQLAlchemy y emite/valida JWT ligados a `device_id` con PyJWT.

Expone:

- `POST /sesiones` — crea o renueva una sesión activa y emite su JWT (`ISesiones`).
- `GET /sesiones/<session_id>` — consulta el estado actual de una sesión.
- `POST /sesiones/<session_id>/revocar` — revoca la sesión, con efecto inmediato en consultas posteriores.
- `POST /sesiones/validar` — dispositivo registrado, última actividad y si la sesión está activa; lo consume el Detector (`IValidarSesión`).
- `POST /verificacion/iniciar` — inicia la verificación reforzada de una sesión sospechosa y la marca `pendiente_verificacion`; lo consume el Gateway (`IIdentidad`).
- `POST /jwt/validar` — valida un JWT emitido por el servicio.

Se configura con `SECRETO_JWT`, `ALGORITMO_JWT`, `URL_BASE_DATOS_IDENTIDAD` (por defecto `sqlite:///identidad_sesiones.db`) y `TTL_SESION_SEGUNDOS`. Para correrlo de forma aislada: `python -m identidad.servidor`.

## Experimento de seguridad (HA16)

El paquete `generador/` produce el tráfico del experimento y `analisis/` consolida sus métricas. Instala sus dependencias con `python -m pip install -e ".[test,carga,analisis]"`.

Cada petición se registra junto con la etiqueta de lo que *debería* ocurrirle (`legitima` o `suplantada`). Esa etiqueta es lo que permite calcular la matriz de confusión: un `403` sobre un ataque y un `403` sobre un cliente legítimo son el mismo código de respuesta y sin ella no se distinguen.

### Corrida completa

Con el stack en marcha:

```bash
python -m generador.run_escenario --escenario TODOS --repeticiones 3 --servicio-compose detector
python -m analisis.main
```

`--servicio-compose detector` es necesario con Docker: la base del Detector vive en un volumen del contenedor, y sembrarla desde el host dejaría al servicio con la referencia de la corrida anterior. Sin Docker se omite, y se indica `--url-base-datos-deteccion` si no es la predeterminada. El orquestador comprueba después de sembrar que el Detector parte de la ubicación esperada, y aborta si no.

### Escenarios

| ID | Escenario | Qué responde |
|---|---|---|
| A | Legítimo puro | Falsos positivos sin ataques presentes |
| B | Suplantación por dispositivo | Detección de la regla de dispositivo |
| C | Suplantación por ubicación | Detección de la regla de ubicación |
| D | Frontera del radio | Si el umbral discrimina donde declara |
| E | Mezcla realista | Latencia y clasificación con tráfico mixto |

D es el único que puede refutar la regla: barre distancias alrededor de `RADIO_UBICACION_KM` y produce la curva de detección frente al desplazamiento.

### Parámetros

Están centralizados en `parametros/`, todos con sobreescritura por variable de entorno, y cada corrida deja en su `manifest.json` los valores efectivos, la semilla y si el Detector era el real o un doble.

### Cómo leer los resultados

Una métrica cuyo denominador es cero se reporta como `N/A`, no como cero: en el escenario A la tasa de detección es *indefinida* porque no hay suplantaciones, y presentarla como 0 % afirmaría que el sistema no detectó nada. Cada tasa usa su propio denominador —la detección sobre las suplantadas, los falsos positivos sobre las legítimas, la precisión sobre las suspendidas— y la latencia se reporta separada por veredicto, porque suspender y enrutar recorren caminos de distinta longitud.

Las corridas medidas se mantienen por debajo de dos minutos: pasada la ventana de actividad reciente del Detector, la regla de ubicación deja de aplicarse y las suplantaciones por ubicación se contarían como falsos negativos que son un artefacto de la duración.

## Limitaciones

El journey sigue siendo un doble determinista; representa la operación protegida y no una implementación real. Detector e Identidad sí son implementaciones reales.

El contexto de sesión (dispositivo y ubicación) lo declara el propio cliente en `X-Session-Context`, de modo que un suplantador puede informar cualquier ubicación. El experimento mide el mecanismo de detección, no la fiabilidad de esa fuente.

La regla de ubicación detecta saltos, no desplazamientos graduales: como cada veredicto normal reescribe la última ubicación conocida, una secuencia de peticiones dentro del radio mueve la referencia sin ser detectada.