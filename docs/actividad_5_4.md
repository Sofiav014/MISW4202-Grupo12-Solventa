# Actividad 5.4 — Guardar resultados

Recibe evidencia de una corrida **ya ejecutada**, valida su integridad técnica y
completa `resultados/escenario_<A-G>/<corrida_id>/`. No requiere instalar paquetes,
levantar servicios ni configurar variables del adaptador (Python 3.10+).

## Uso

Desde la raíz del repositorio, para empaquetar todas las corridas ya existentes:

```bash
python load-testing/run_escenario.py guardar-resultados --manifests-dir resultados --log-compartido resultados/adaptador.jsonl --resultados-dir resultados
```

5.4 lee el log compartido una vez, toma los eventos `request`, los separa por
**(escenario, ejecucion_id)** y valida cada grupo contra su manifest. No exige un
`requests.csv` previo. Antes de publicar valida todas las entradas del lote;
rechaza identidades sin manifest, carpetas sin manifest y corridas sin peticiones.

Para una sola corrida se usa `--manifest ruta/manifest.json --log-compartido
resultados/adaptador.jsonl`. Se mantiene la alternativa `--manifest ... --records
entrada/requests.csv` (o `.jsonl`) para entradas ya separadas. En esa alternativa,
el núcleo rechaza mezclas en vez de filtrarlas.

Para adjuntar CSV agregados a una sola corrida, agregar `--adjunto
entrada/results_stats.csv`; el flag puede repetirse. En lote se toman los CSV
de cada carpeta de origen. Los adjuntos reconocidos que ya estén en el destino
se incluyen y validan igualmente.
La raíz predeterminada es `resultados` del repo; no se consulta `LOG_DIR`.
Imprime la ruta final y devuelve **0** en éxito, **1** ante errores de evidencia/IO
y **2** ante argumentos inválidos. Los eventos de guardado se escriben como JSON
en stderr. No se añade guardado automático al runner de 5.3.

## Entradas y contratos

- **Manifest de 4.3:** JSON de `experimentos.manifest.ManifestCorrida`, con
  `corrida_id`, `escenario`, `timestamp` con zona horaria, `circuit_breaker`,
  `cache`, `mock_openfinance`, `carga` y `provider_timeout_ms`. Se reutilizan sus
  DTO y validaciones, conservando todos los bytes originales y campos adicionales.
  No se genera un manifest alternativo ni se consultan condiciones actuales.
- **Log compartido:** registros del contrato 0.4 producidos por la instrumentación
  existente, con un objeto JSON por línea y `event_type=request` en las peticiones.
  Los eventos auxiliares de logging se omiten explícitamente; se exige JSON
  legible también en ellos. Una petición malformada no se omite silenciosamente.
  Se conserva el orden de las peticiones dentro de cada grupo.
- **Registros de una corrida:** CSV con cabecera o JSONL por petición, producido
  por el adaptador de log compartido de 5.4 o recibido directamente. Son obligatorios `request_id`,
  `ejecucion_id` y `escenario`, como strings no vacíos, en **cada** registro.
  `ejecucion_id` debe coincidir con `corrida_id` del manifest. La identidad es el
  par **(escenario, corrida)**, ya que una misma corrida puede nombrarse igual en A–G.
- **Campos adicionales:** se conservan sin calcularlos. Estados, latencias y
  datos de caché son opcionales para 5.4. Si el lote declara `event_type`, cada
  fila debe contener `request`; se rechazan eventos de Werkzeug, caché y breaker.
  El lector de una corrida rechaza eventos ajenos; el de log compartido selecciona
  expresamente `request` y agrupa identidades sin perder peticiones.
- **Adjuntos opcionales:** `results_stats.csv`, `results_stats_history.csv`,
  `results_failures.csv` y `results_exceptions.csv`. Deben incluir las columnas
  que ya producen los archivos actuales. Fallos y excepciones pueden tener solo
  cabecera; los otros dos necesitan filas. No se calculan sus estadísticas.

La salida tabular conserva el orden de peticiones y todas las columnas: identidad
primero y extras en orden alfabético. CSV usa UTF-8, coma, comillas estándar y
salto LF; la entrada también admite BOM. Los campos ausentes/nulos opcionales se
representan como celdas vacías; booleanos como `true`/`false`, y objetos/listas
como JSON compacto. CSV no conserva la distinción entre null y string vacío.

## Validación y persistencia

Se comprueban existencia de archivos regulares, JSON válido sin claves duplicadas
ni NaN/Infinity, CSV legible con cabeceras únicas y filas de anchura correcta,
al menos una petición, campos de trazabilidad, escenarios A–G y coincidencia de
todas las identidades. Los adjuntos se validan estructuralmente; `Name` permite
comprobar `/cotizar[<escenario>]` cuando existe (también se admite `Aggregated`).
Se revisan identificadores adicionales si el adjunto los incluye.

Se reutilizan los identificadores portables de 4.3 y se rechazan escapes de ruta,
symlinks y junctions bajo el destino. El manifest o adjunto preexistente solo se
reutiliza si sus **bytes coinciden** con la entrada; nunca se sobrescribe.
La existencia de `results.csv`, `procedencia.json` o `integridad.json` causa un
error de colisión. Volver a ejecutar el comando no reemplaza paquetes existentes.

La escritura usa temporales en el mismo filesystem, `flush`/`fsync` y publicación
mediante hard links exclusivos. Requiere un filesystem local con hard links
(por ejemplo NTFS o ext4); si no los soporta, devuelve un error de IO sin recurrir
a una escritura parcial. Un lock exclusivo coordina guardados 5.4 de la misma
corrida. Ante errores controlados se retiran únicamente los archivos de la
operación y se registran también los errores de limpieza.

`integridad.json` se publica **al final**. Incluye `version: 1`, `estado: "valido"`,
`corrida_id`, `escenario`, `cantidad_registros` e inventario `archivos`
(nombre → SHA-256). Su contenido es determinista y no incluye métricas ni marcas
de tiempo artificiales. Los hashes detectan cambios; no autentican al productor.

Para entradas compartidas se añade `procedencia.json`, incluido en los hashes:
nombre y SHA-256 del archivo fuente, criterio de selección, confirmación de
ventana y limitaciones. La identidad concreta y el manifest se encuentran en
el propio paquete y su constancia. Esto permite rastrear cada CSV al snapshot
del log sin copiar el log completo en las 21 carpetas.

```text
manifests reales + adaptador.jsonl compartido
                    ↓
    5.4 separa por escenario y ejecucion_id
                    ↓
       validación técnica y de identidad
                    ↓
         persistencia sin sobrescritura
                    ↓
resultados/escenario_B/<corrida_id>/
├── manifest.json
├── results.csv
├── procedencia.json   # origen, criterio y limitaciones
├── integridad.json
└── results_*.csv       # adjuntos opcionales
```

La ausencia de la constancia significa que el paquete no está validado. Una
terminación abrupta puede dejar temporales/lock o archivos sin constancia; se
reporta la colisión al reintentar, sin borrado automático de evidencia previa.
El operador debe revisar esos residuos antes de retirarlos. El productor debe
haber cerrado sus archivos: el lock de 5.4 no controla escrituras de 5.3.
La publicación es por paquete, no una transacción global: un fallo de IO durante
un lote conserva los paquetes anteriores ya publicados y devuelve código 1.

## API y sustitución de bordes

```python
from experimentos.resultados import (
    AlmacenResultadosLocal, guardar_resultados,
    preparar_corridas_desde_log, validar_paquete,
)

entradas = preparar_corridas_desde_log("resultados", "resultados/adaptador.jsonl")
for entrada in entradas:
    ruta = guardar_resultados(entrada, AlmacenResultadosLocal("resultados"))
    verificada = validar_paquete(ruta)  # solo lectura: hashes, inventario y contenido
```

Otro origen puede construir `EntradaManifest`/`TablaRegistros` y utilizar el
mismo servicio. Otro formato de almacenamiento implementará
`AlmacenResultados.guardar(EvidenciaCorrida) -> Path`; las reglas puras de
`validacion.py` se mantienen. `validar_paquete` es el verificador del formato
local documentado, no una interfaz universal para otros almacenes.

## Dependencias, fixtures y límites

**No hay stubs de producción:** manifests y logging existen, y 5.4 implementa
la separación del archivo compartido. Las únicas
entradas artificiales son fixtures de tests, en `tests/fixtures_resultados.py`.

```text
Stub: fixture de registros de una corrida, exclusivo de tests.
Responsabilidad real: emitir los registros individuales del contrato 0.4.
Productor: instrumentación existente (actividad 4.1 según el contrato experimental).
Contrato: JSONL estructurado con event_type=request, request_id, ejecucion_id y escenario.
Reemplazo: el flujo real ya usa adaptador.jsonl; los fixtures solo aíslan las pruebas.
```

**Limitación contractual de la ventana de medición:** el JSONL histórico contiene
warm-up y preparación con las mismas identidades. `correr_repeticion` espera que
termine el warm-up, prepara condiciones, guarda el manifest y después lanza
Locust. El manifest no guarda el inicio/fin reales y puede reutilizarse al repetir
una identidad. `timestamp_inicio/fin` del log son monotónicos del proceso; no son
fechas UTC comparables al manifest. `ts_wall` sí es UTC, pero no identifica la
fase. Los `Timestamp` de `results_stats_history.csv` son muestras periódicas: su
última muestra precede al último evento del log, no es una marca de fin.

La información actual no permite certificar ambos límites de la medición.
Por eso 5.4 conserva **todas** las peticiones de cada identidad, sin recortar con
el timestamp del manifest, su duración configurada o la última muestra de Locust.
Registra `ventana_medicion_confirmada: false` y sus razones en cada procedencia;
esa limitación no bloquea la integridad técnica ni el empaquetado. Para aislar
medición en el futuro se necesitan límites reales comparables o una marca de
fase por petición. No se implementan esos productores ni se modifica 5.3.

Los CSV agregados actuales no contienen `corrida_id` ni `request_id`: se vinculan
al paquete suministrado, sin poder demostrar por sí mismos su corrida de origen.
El runner también permite conservar un manifest anterior al reescribir CSV.
5.4 verifica consistencia visible y bytes recibidos, pero no certifica condiciones
históricas, ausencia de peticiones perdidas ni completitud estadística.

Quedan fuera de alcance 4.1/4.2, generación 4.3, configuración/preparación/ejecución
5.1–5.3, escenarios A–G, Locust, Circuit Breaker, Redis, mock OpenFinance,
instrumentación por petición, métricas, gráficas y conclusiones de Fase 6.
Se añaden los artefactos de 5.4 junto a los históricos, conservando los bytes del
manifest, los CSV agregados y el log compartido original.

## Pruebas

```bash
python -m unittest tests.test_manifest tests.test_resultados tests.test_resultados_cli tests.test_log_compartido -v
git diff --check
```

Las pruebas usan `TemporaryDirectory`, fixtures y fallos de IO inyectados. El CLI
se prueba además con `python -S` para excluir dependencias de infraestructura.
