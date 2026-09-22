# Análisis de resultados

Procesamiento en Pandas de los datos reales del experimento para validar el ASR HA2.
No se simula ni se genera ningún dato. Un solo cuaderno (`reporte_analisis.ipynb`)
reúne el análisis hecho hasta ahora: métricas de A, B, C y G; el costo
del disparo frente al circuito ya abierto en B, C y D; y la recuperación del breaker en E.

## Ejecución

Se usa el entorno virtual del repositorio (`venv/` en la raíz):

```bash
source venv/bin/activate
pip install pandas matplotlib jupyter

python -m analisis.main                  # tablas + gráficas
python -m analisis.main --sin-graficas   # solo las tablas
```

## Organización

```text
analisis/
├── main.py              # punto de entrada único
├── procesamiento/       # el motor: se importa, no se ejecuta
│   ├── carga.py         # lee el JSONL, valida esquema, clasifica poblaciones
│   ├── metricas.py      # una función pura por métrica
│   ├── locust.py        # throughput y contraste externa/interna
│   ├── graficas.py      # una función por figura
│   ├── recuperacion.py  # recuperación del breaker en E (lee la evidencia guardada, no ejecuta nada)
│   └── salidas/         # 6 tablas CSV y 10 gráficas PNG (la recuperación de E no exporta)
└── reportes/
    └── reporte_analisis.ipynb   # A, B, C, G + B, C, D + E
```

La lógica vive en `procesamiento/` y no en el cuaderno para que el análisis de F
(condición límite de caché vacía) reutilice el mismo motor con un `import`, con una
sola definición por fórmula.

## Fuente de datos: JSONL, no los CSV de Locust

Los `results_stats.csv` son agregados por endpoint, sin estado del circuito,
`hit_miss` ni `tiempo_conmutacion_ms`, así que no permiten validar el ASR por sí
solos y la desviación debe declararse en el informe. Además Locust no distingue
una respuesta del proveedor de una degradada desde caché (ambas son HTTP 200).

La fuente es **`resultados/adaptador.jsonl`**: 27.533 peticiones, una fila cada una,
con el esquema de instrumentación. De Locust solo salen el throughput y el
contraste entre latencia externa e interna.

## El cuidado de método

`clasificar_poblacion()` separa tres poblaciones con costos muy distintos, y toda
métrica de latencia y conmutación se reporta también desglosada por ellas:

- **TRIGGER** — la petición que dispara el corte; paga el timeout completo.
- **CIRCUITO_ABIERTO** — encuentra el circuito ya abierto y va directo a caché.
- **NORMAL** — circuito cerrado, respuesta del proveedor sin degradación.

En B las TRIGGER pagan 589 ms (p50) y las 5.158 siguientes 0,71 ms: **~800× de
diferencia**. Como son el 2,5 % de la muestra, el agregado esconde el costo del corte.

`desglose_trigger()` va un nivel más abajo: separa, dentro de TRIGGER, el disparo
inicial de los reintentos `HALF_OPEN` que vuelven a fallar mientras el proveedor
sigue degradado, y mide qué fracción de la ventana `OPEN` esos reintentos igual le
devuelven al proveedor. El cuaderno lo desarrolla con D como contraste — el
escenario que aísla CIRCUITO_ABIERTO forzando el corte antes de medir, en vez de
dejar que el propio tráfico lo dispare.

## Resultados

| Meta | Resultado |
|---|---|
| Disponibilidad ≥99,9 % | **100 %** en A, B, C y G — cero fallidos en 18.217 peticiones |
| Conmutación <1 s | **100 %** de las 10.938 conmutaciones de B y C (máx **6,4 ms**) |

### Salvedades

1. **G no prueba degradación bajo carga.** Corrió con `modo=normal`; su latencia de
   ~4 s es saturación del mock, no costo del mecanismo, que allí nunca se activó. Lo
   que sí demuestra: no hay conmutaciones espurias bajo carga (0 de 4.642).
2. **A y G no ejercitan el ASR.** Cero conmutaciones: las métricas de conmutación y
   hit rate quedan **indefinidas** (`N/A`), no cumplidas ni incumplidas.
3. **El 100 % de disponibilidad supone caché poblada.** El escenario F (caché vacía)
   es la condición límite y se reporta aparte.
4. **El registro interno tiene ~5 % más peticiones que Locust** (A: 773 vs 708): el
   Adaptador registra peticiones fuera de la ventana de medición de Locust. No afecta
   las métricas, que son proporciones.
5. **El contraste externa/interna se lee en la mediana** (+6 a +23 ms de red y cola).
   En los percentiles altos cada fuente mide una población distinta.

## Alcance

El motor soporta A–G. Ya están resueltos: las tablas y gráficas de A, B, C y G; el
desglose del disparo del corte frente al circuito ya abierto en B, C y D; y la
recuperación del breaker en E, con verificación de integridad contra la evidencia
guardada. Los tres están integrados en `reporte_analisis.ipynb`. Queda pendiente
la condición límite de caché vacía (F).

**Nota de reproducibilidad para la recuperación de E:** sus celdas se preservan en el
cuaderno con la salida ya calculada, sin re-ejecutar, porque `recuperacion.load_results()`
verifica el hash SHA-256 de cada archivo de `resultados/escenario_E/*` contra el que
registra su `integridad.json`, y ahora mismo `manifest.json` no coincide con ese hash —
quedaron en commits distintos (`50b4d70` y `7b89423`). No es un problema introducido por
este cuaderno; re-ejecutar esa sección en limpio requiere antes resolver esa
discrepancia de datos.
