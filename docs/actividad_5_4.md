# Guardar resultados

Recibe evidencia de una ejecución **ya realizada**, valida su integridad técnica y
completa `resultados/escenario_<A-G>/<corrida_id>/`. No requiere instalar paquetes,
levantar servicios ni configurar variables del adaptador (Python 3.10+).

## Uso

Desde la raíz del repositorio, para empaquetar todas las ejecuciones ya existentes:

```bash
python load-testing/run_escenario.py guardar-resultados --manifests-dir resultados --log-compartido resultados/adaptador.jsonl --resultados-dir resultados
```

El comando lee el log compartido una vez, toma los eventos `request`, los separa por
**(escenario, ejecucion_id)** y valida cada grupo contra su manifest. No exige un
`requests.csv` previo. Antes de publicar valida todas las entradas del lote;
rechaza identidades sin manifest, carpetas sin manifest y ejecuciones sin peticiones.

Para una sola ejecución se usa `--manifest ruta/manifest.json --log-compartido
resultados/adaptador.jsonl`. Se mantiene la alternativa `--manifest ... --records
entrada/requests.csv` (o `.jsonl`) para entradas ya separadas. En esa alternativa,
el núcleo rechaza mezclas en vez de filtrarlas.

Para adjuntar CSV agregados a una sola ejecución, agregar `--adjunto
entrada/results_stats.csv`; el flag puede repetirse. En lote se toman los CSV
de cada carpeta de origen. Los adjuntos reconocidos que ya estén en el destino
se incluyen y validan igualmente.
La raíz predeterminada es `resultados` del repo; no se consulta `LOG_DIR`.
Imprime la ruta final y devuelve **0** en éxito, **1** ante errores de evidencia/IO
y **2** ante argumentos inválidos. Los eventos de guardado se escriben como JSON
en stderr. No se añade guardado automático al runner de carga.

## Entradas y contratos

- **Manifest:** JSON de `experimentos.manifest.ManifestCorrida`, con
  `corrida_id`, `escenario`, `timestamp` con zona horaria, `circuit_breaker`,
  `cache`, `mock_openfinance`, `carga` y `provider_timeout_ms`. Se reutilizan sus
  DTO y validaciones, conservando todos los bytes originales y campos adicionales.
  No se genera un manifest alternativo ni se consultan condiciones actuales.
- **Log compartido:** registros de instrumentación producidos por el adaptador,
  con un objeto JSON por línea y `event_type=request` en las peticiones.
  Los eventos auxiliares de logging se omiten explícitamente; se exige JSON
  legible también en ellos. Una petición malformada no se omite silenciosamente.
  Se conserva el orden de las peticiones dentro de cada grupo.
- **Registros de una ejecución:** CSV con cabecera o JSONL por petición, producido
  por el adaptador de log compartido o recibido directamente. Son obligatorios `request_id`,
  `ejecucion_id` y `escenario`, como strings no vacíos, en **cada** registro.
  `ejecucion_id` debe coincidir con `corrida_id` del manifest. La identidad es el
  par **(escenario, ejecución)**, ya que una misma ejecución puede nombrarse igual en A–G.
- **Campos adicionales:** se conservan sin calcularlos. Estados, latencias y
  datos de caché son opcionales. Si el lote declara `event_type`, cada
  fila debe contener `request`; se rechazan eventos de Werkzeug, caché y breaker.
  El lector de una ejecución rechaza eventos ajenos; el de log compartido selecciona
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

Se reutilizan los identificadores portables del manifest y se rechazan escapes de
ruta, symlinks y junctions bajo el destino. El manifest o adjunto preexistente solo se
reutiliza si sus **bytes coinciden** con la entrada; nunca se sobrescribe.
La existencia de `results.csv`, `procedencia.json` o `integridad.json` causa un
error de colisión. Volver a ejecutar el comando no reemplaza paquetes existentes.

La escritura usa temporales en el mismo filesystem, `flush`/`fsync` y publicación
mediante hard links exclusivos. Requiere un filesystem local con hard links
(por ejemplo NTFS o ext4); si no los soporta, devuelve un error de IO sin recurrir
a una escritura parcial. Un lock exclusivo coordina guardados de la misma
ejecución. Ante errores controlados se retiran únicamente los archivos de la
operación y se registran también los errores de limpieza.

`integridad.json` se publica **al final**. Incluye `version: 1`, `estado: "valido"`,
`corrida_id`, `escenario`, `cantidad_registros` e inventario `archivos`
(nombre → SHA-256). Su contenido es determinista y no incluye métricas ni marcas
de tiempo artificiales. Los hashes detectan cambios; no autentican al productor.

Para entradas compartidas se añade `procedencia.json`, incluido en los hashes:
nombre y SHA-256 del archivo fuente, criterio de selección, confirmación de
ventana y limitaciones. La identidad concreta y el manifest se encuentran en
el propio paquete y su constancia. Así se rastrea cada CSV al snapshot
del log sin copiar el log completo en las 21 carpetas.

```text
manifests reales + adaptador.jsonl compartido
                    ↓
      separación por escenario y ejecucion_id
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
haber cerrado sus archivos: el lock de guardado no controla las escrituras del
proceso que ejecuta el experimento.
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

**No hay stubs de producción:** manifests y logging existen, y la separación del
archivo compartido está implementada. Las únicas
entradas artificiales son fixtures de tests, en `tests/fixtures_resultados.py`.

```text
Stub: fixture de registros de una ejecución, exclusivo de tests.
Responsabilidad real: emitir los registros individuales de instrumentación.
Productor: instrumentación existente del adaptador.
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
Por eso se conservan **todas** las peticiones de cada identidad, sin recortar con
el timestamp del manifest, su duración configurada o la última muestra de Locust.
Registra `ventana_medicion_confirmada: false` y sus razones en cada procedencia;
esa limitación no bloquea la integridad técnica ni el empaquetado. Para aislar
medición en el futuro se necesitan límites reales comparables o una marca de
fase por petición.

Los CSV agregados actuales no contienen `corrida_id` ni `request_id`: se vinculan
al paquete suministrado, sin poder demostrar por sí mismos su ejecución de origen.
El runner también permite conservar un manifest anterior al reescribir CSV.
La validación verifica consistencia visible y bytes recibidos, pero no certifica
condiciones históricas, ausencia de peticiones perdidas ni completitud estadística.

Quedan fuera de alcance la instrumentación por petición, la generación del manifest,
la configuración, preparación y ejecución de los escenarios, Locust, Circuit Breaker,
Redis, el mock OpenFinance, las métricas, gráficas y conclusiones del análisis.
Se añaden los artefactos de guardado junto a los históricos, conservando los bytes del
manifest, los CSV agregados y el log compartido original.

## Pruebas

```bash
python -m unittest tests.test_manifest tests.test_resultados tests.test_resultados_cli tests.test_log_compartido -v
git diff --check
```

Las pruebas usan `TemporaryDirectory`, fixtures y fallos de IO inyectados. El CLI
se prueba además con `python -S` para excluir dependencias de infraestructura.
