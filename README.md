# Solventa Gateway

Este repositorio empaqueta el límite de políticas del gateway junto con stubs HTTP deterministas. El gateway autentica y valida las solicitudes, evalúa el Detector, inicia la verificación de Identidad para sesiones sospechosas y únicamente redirige al servicio Journey las sesiones normales.

## Instalación limpia

Instala el proyecto y sus herramientas de pruebas con `python -m pip install -e ".[test]"` y luego ejecuta `python -m pytest`.

En un entorno limpio, puedes utilizar `python -m venv .venv`, seguido de `.venv/bin/python -m pip install -e ".[test]"` y `.venv/bin/python -m pytest` (en Windows, utiliza `.venv\Scripts\python.exe`).

## Ejecución rápida con Compose

Copia `.env.example` como `.env` y luego ejecuta `docker compose config` y `docker compose up --build`.

El gateway estará disponible en http://localhost:8080. La selección de escenarios del stub es exclusiva para pruebas y se realiza mediante `X-Escenario-Stub`.

Una solicitud HS256 válida debe incluir `Authorization` y `X-Session-Context`. Las solicitudes normales devuelven la respuesta del stub de Journey; `X-Escenario-Stub: sospechoso` devuelve `403` después de invocar Identidad y nunca llama a Journey.

`PERMITIR_ESCENARIO_STUB` tiene como valor predeterminado `false`, por lo que los clientes HTTP ordinarios nunca reenvían el encabezado de escenario. El ejemplo de Compose lo habilita únicamente para el perfil de pruebas deterministas; la lista de encabezados funcionales permitidos de Journey permanece sin cambios.

## Servicio de Identidad (ms-identidad-sesiones)

`ms-identidad-sesiones` (paquete `identidad/`) es la implementación real del componente de Identidad y reemplaza al stub `dobles_http` en el servicio `identity` de Compose. Persiste sesiones en SQLite vía SQLAlchemy y emite/valida JWT ligados a `device_id` con PyJWT.

Expone:

- `POST /sesiones` — crea o renueva una sesión activa y emite su JWT (`ISesiones`).
- `GET /sesiones/<session_id>` — consulta el estado actual de una sesión.
- `POST /sesiones/<session_id>/revocar` — revoca la sesión, con efecto inmediato en consultas posteriores.
- `POST /sesiones/validar` — dispositivo registrado, última actividad y si la sesión está activa; lo consume el Detector (`IValidarSesión`).
- `POST /verificacion/iniciar` — inicia la verificación reforzada de una sesión sospechosa y la marca `pendiente_verificacion`; lo consume el Gateway (`IIdentidad`).
- `POST /jwt/validar` — valida un JWT emitido por el servicio.

Se configura con `SECRETO_JWT`, `ALGORITMO_JWT`, `URL_BASE_DATOS_IDENTIDAD` (por defecto `sqlite:///identidad_sesiones.db`) y `TTL_SESION_SEGUNDOS`. Para correrlo de forma aislada: `python -m identidad.servidor`.

## Medición

Con Compose en ejecución, ejecuta:

`python scripts/medir_latencia_gateway.py --requests 100 --scenario normal`

y repite la medición con:

`--scenario sospechoso`

El JSON reporta el p95 del gateway, el p95 de la suspensión de sesiones sospechosas y `detector_stub_observed_p95_ms`, que corresponde a una medición HTTP directa e independiente del stub del Detector —incluyendo la sobrecarga HTTP local— y no a la latencia interna del Detector.

Compáralo con `detector_budget_ms`. Estos valores validan únicamente el comportamiento del gateway y de los stubs, y no pretenden demostrar la precisión del Detector, la capacidad en producción ni la hipótesis del experimento integrado.

## Limitaciones

Detector y Journey siguen siendo stubs deterministas implementados con Flask; Identidad ya es una implementación real (`identidad/`). Los contadores de los stubs son locales a cada proceso y todavía no se incluye ningún modelo de carga con Locust/Pandas.