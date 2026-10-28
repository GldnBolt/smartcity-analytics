# MongoDB — SmartCity Analytics (datos reales de Madrid)

Esta parte guarda los datos **semiestructurados y de alta velocidad** que generan los sensores
IoT: lecturas, eventos, alertas e incidentes. Los datos son **mediciones reales** publicadas por
el Ayuntamiento de Madrid (datos.madrid.es, licencia CC BY 4.0) para 2024–2025. La única
excepción es la energía, que es sintética (ver más abajo).

Los datos maestros (sensores, barrios, distritos y umbrales) viven en **PostgreSQL, la fuente de
verdad**. El importador los **lee de PostgreSQL** al arrancar, así que PostgreSQL se carga primero.

## Cómo ejecutarlo

Requisito único: Docker Desktop. No hace falta instalar Python ni mongosh.

```powershell
docker compose run --rm ingesta    # 1. descarga los datos abiertos a data/raw/madrid (~2,2 GB, una sola vez)
.\cargar_todo.ps1                  # 2. desde la raíz: PostgreSQL y luego MongoDB (~3 min)
sh cargar_todo.sh                  #    Linux / macOS / Git Bash
```

Solo MongoDB (con PostgreSQL ya cargado):

```powershell
docker compose exec -T mongo mongosh --quiet /scripts/00_esquema.js
docker compose run --rm importador
docker compose exec -T mongo mongosh --quiet /scripts/01_indices.js
```

Después se puede ejecutar cualquiera de estos scripts:

```powershell
docker compose exec mongo mongosh /scripts/02_crud.js
docker compose exec mongo mongosh /scripts/03_consultas_obligatorias.js
docker compose exec mongo mongosh /scripts/04_experimentos.js
docker compose run --rm importador python -m mongodb.consultas   # prueba del módulo del dashboard
```

En Git Bash hay que anteponer `MSYS_NO_PATHCONV=1` a los `docker compose exec`. Para conectarse
con Compass o con Streamlit se usa `mongodb://localhost:27017`, base `smartcity`.

## Archivos

| Archivo | Qué contiene |
|---|---|
| [`../ingesta/descargar_madrid.py`](../ingesta/descargar_madrid.py) | Descarga reproducible desde el API del portal, con manifiesto de procedencia ([`data/raw/README.md`](../data/raw/README.md)) |
| [`importador/fuentes.py`](importador/fuentes.py) | Lectores de cada fuente real (aire, clima, tráfico, ruido, aparcamientos, Calle 30) y el modelo sintético de energía |
| [`importador/cargar_mongo.py`](importador/cargar_mongo.py) | Lee sensores y umbrales de PostgreSQL; importación masiva con `insertMany` y cálculo de alertas y eventos |
| [`scripts/00_esquema.js`](scripts/00_esquema.js) | Colecciones con validación `$jsonSchema` |
| [`scripts/01_indices.js`](scripts/01_indices.js) | 17 índices: compuestos, 2dsphere, parcial, multikey y de texto |
| [`scripts/02_crud.js`](scripts/02_crud.js) | insertOne/Many, find/findOne, updateOne/Many, deleteOne, operadores, proyecciones, `$text`, `$geoNear`, `$elemMatch` |
| [`scripts/03_consultas_obligatorias.js`](scripts/03_consultas_obligatorias.js) | Las 10 consultas obligatorias y 7 preguntas del enunciado |
| [`scripts/04_experimentos.js`](scripts/04_experimentos.js) | Evidencia experimental: índices, time series e ingesta |
| [`consultas.py`](consultas.py) | Las mismas consultas en Python, con filtros (fecha, distrito, tipo, sensor) y KPIs, para Streamlit |

## Fuentes y volumen (2024-01-01 a 2025-12-31)

| Tipo | Fuente real (datos.madrid.es) | Sensores | Granularidad | Lecturas |
|---|---|---:|---|---:|
| calidad_aire | Calidad del aire. Datos horarios | 24 estaciones | horaria | 408 411 |
| clima | Datos meteorológicos horarios | 26 estaciones | horaria | 440 094 |
| trafico | Histórico de tráfico (SICTRAM) | 63 puntos seleccionados | 15 min, agregada a horaria | 892 773 |
| ruido | Contaminación acústica. Datos diarios | 31 estaciones | diaria (día, tarde, noche, 24 h) | 22 468 |
| estacionamiento | Aparcamientos. Histórico de ocupación | 56 aparcamientos | mensual | 1 192 |
| energia | **SINTÉTICA** (no hay dato abierto horario) | 21 medidores (1 por distrito) | horaria | 368 424 |
| **Total lecturas** | | **221** | | **2 133 362** (1,76 M reales) |

Además: **43 227 alertas**, **8 849 eventos** y **4 127 incidentes reales** de Calle 30.
El mínimo exigido es de 500 000 documentos.

**Selección de puntos de tráfico.** Madrid tiene unos 5 000 puntos de medida (unos 320 millones
de registros en dos años, alrededor de 19 GB). Ese histórico completo es para HDFS y Spark. En
MongoDB entran 63 puntos: los 2 más cercanos a cada estación de calidad del aire (para poder
correlacionar tráfico y contaminación), al menos 2 por distrito y 8 de Calle 30, que son los
únicos que miden velocidad.

**Energía sintética.** El portal solo publica consumos mensuales de edificios municipales. El
consumo horario se modela como base del distrito × perfil horario × factor de temperatura
(calefacción por debajo de 16 °C y aire acondicionado por encima de 24 °C) × fin de semana ×
tendencia × ruido. La **temperatura es la real** de la red meteorológica de Madrid en cada hora.
Esas lecturas llevan `meta.fuente: "sintetico"`, y en PostgreSQL esos sensores tienen
`fuente_datos = 'sintetico'`.

## Modelo documental

### `lecturas`: una medición por documento

```js
{
  sensor_id: "AIR-008",                  // = sensores.codigo en PostgreSQL
  tipo: "calidad_aire",
  ts: ISODate("2025-12-31T22:00:00Z"),   // UTC; las consultas usan timezone Europe/Madrid
  ubicacion: {                           // copia (desnormalización) del maestro de PostgreSQL
    zona_id: "D04", zona: "Salamanca", area: "Almendra central", barrio: "Recoletos",
    geo: { type: "Point", coordinates: [-3.682316, 40.421553] }   // GeoJSON → índice 2dsphere
  },
  mediciones: { no2: 38, no: 12, nox: 56, pm10: 14, pm25: 7, o3: 21, co: 0.3, so2: 4,
                benceno: 0.4, tolueno: 1.9, etilbenceno: 0.3 },
  meta: { fuente: "datos.madrid.es", tipo_estacion: "Urbana tráfico",
          no_validados: { pm25: 0 } }    // valores que la red marcó como NO validados ('N')
}
```

El contenido de `mediciones` **cambia según el tipo de sensor e incluso entre estaciones del
mismo tipo**:

| tipo | campos en `mediciones` |
|---|---|
| calidad_aire | subconjunto de no2, no, nox, pm10, pm25, o3, co, so2, benceno, tolueno, etilbenceno. **Escuelas Aguirre mide 11; Arturo Soria, 4** (consulta P7). |
| clima | subconjunto de temperatura, humedad, precipitacion, viento_ms, viento_dir, presion_hpa, radiacion_wm2 |
| trafico | intensidad (veh/h), ocupacion_pct, carga, indice_congestion (+ velocidad_prom y tiempo_km_min **solo en Calle 30**) |
| ruido | laeq_dia, laeq_tarde, laeq_noche, laeq_24h (+ percentiles l1, l10, l50, l90, l99 si la estación los publica) |
| estacionamiento | ocupacion_pct, ocupacion_9_21_pct, vehiculos_dia, plazas |
| energia | consumo_kwh, demanda_kw, temperatura_ref |

### `alertas`, `eventos`, `incidentes`

- **alertas**: una lectura supera un umbral de la tabla `umbrales` de PostgreSQL **del mismo
  periodo que la lectura** (horario para aire, clima, tráfico y energía; diario para ruido;
  mensual para aparcamientos). Los umbrales se basan en normativa real cuando existe: NO2 200/400
  µg/m³ y O3 180/240 por hora (Directiva 2008/50/CE), ruido Ld 65 / Ln 55 dB(A) (RD 1367/2007),
  etc. Los límites de PM10 y PM2.5 son diarios, así que no generan alertas sobre lecturas
  horarias. En 2024–2025 Madrid no superó ninguna vez el límite horario de NO2;
  sí hubo 141 superaciones del umbral de información de ozono. El ciclo de atención (estado
  `atendida`, operador) **no existe en los datos abiertos**: se simula para poder demostrar el
  flujo y se marca con `atencion.simulada: true`.
- **eventos**: patrones detectados sobre los datos reales: `episodio_contaminacion`,
  `congestion` (carga ≥ 90 %), `lluvia_intensa`, `calor_extremo` (≥ 38 °C), `pico_consumo`,
  `dato_no_validado` (un evento por sensor y día) y `sensor_sin_datos`. Este último son huecos de
  transmisión de más de 12 h, calculados dentro de MongoDB con `$setWindowFields` + `$shift` y
  volcados con `$merge`. El `payload` cambia según el tipo de evento.
- **incidentes**: accidentes reales de Calle 30, con la descripción en texto libre escrita por
  los operadores, `etiquetas` derivadas (túnel, heridos, motocicleta, firme mojado…) y un
  `detalle` que solo contiene lo que ocurrió (heridos, pesados, tipo de colisión…). El portal
  no publica coordenadas, así que se ubican por vía y tramo.

## Por qué MongoDB para estos datos

1. **Esquema heterogéneo real.** Cada estación de Madrid mide un conjunto distinto de variables,
   y cada tipo de sensor tiene las suyas. En SQL habría que elegir entre una tabla ancha con
   decenas de columnas casi siempre nulas (de 24 estaciones de aire, solo unas 7 miden PM2.5),
   una tabla por tipo (con UNION en cada consulta) o un modelo EAV (`sensor, variable, valor`)
   que multiplica las filas y obliga a hacer pivotes. En Mongo cada documento trae solo lo que
   midió.
2. **Evolución sin migraciones.** Si la red agrega un contaminante a una estación, o se incorpora
   un tipo de sensor nuevo (`flujo_peatonal` en `02_crud.js`), no hace falta `ALTER TABLE` ni
   detener la ingesta. La validación `$jsonSchema` asegura lo mínimo común (sensor, `ts`,
   ubicación GeoJSON) sin rigidizar `mediciones`.
3. **El documento es la unidad de lectura.** Un accidente con su detalle, etiquetas y comentarios
   se lee y se escribe en una sola operación atómica.
4. **Ingesta a alta velocidad.** `insertMany` por lotes alcanza unas 59 000 lecturas/s en un
   portátil (experimento C). La importación completa de 2,1 M de lecturas tarda unos 2 minutos.
5. **Capacidades nativas para IoT:** índices 2dsphere y `$geoNear`, time series collections,
   funciones de ventana (`$setWindowFields`), `$objectToArray` para agregar sin conocer el
   esquema, búsqueda de texto en español, y escalamiento horizontal con sharding (por ejemplo,
   clave `{sensor_id: 1, ts: 1}`).

**Desnormalización:** el distrito y las coordenadas se copian dentro de cada lectura. Se
duplica información a cambio de que las agregaciones por zona no necesiten un join. La fuente de
verdad sigue siendo PostgreSQL, que ubica cada sensor en su barrio oficial por geometría (punto
dentro del polígono del barrio).

**Dato tal como lo publica la red.** Los valores marcados como no validados no se descartan: se
guardan en `meta.no_validados`, fuera de `mediciones`, así que no contaminan las estadísticas pero
quedan como evidencia. También se conservan anomalías reales (por ejemplo, horas de un punto de
Calle 30 con intensidad 0 y carga 100 %), que Spark debe limpiar o detectar.

## Mapeo con el enunciado

| Requisito | Dónde |
|---|---|
| Q1 Lecturas de un sensor | `03` Q1 · `consultas.q1_lecturas_sensor` |
| Q2 Rango de fechas | `03` Q2 · `q2_lecturas_rango` |
| Q3 Máximos y mínimos | `03` Q3 (`$objectToArray` + `$top`: valor, sensor y fecha del máximo) · `q3_max_min` |
| Q4 Eventos críticos | `03` Q4 (`$facet`) · `q4_eventos_criticos`, `q4_eventos_por_tipo` |
| Q5 Alertas por zona geográfica | `03` Q5a (`$group`) y Q5b (`$geoNear` + `$bucket` desde la Puerta del Sol) · `q5_*` |
| Q6 Promedio por zona | `03` Q6 · `q6_promedio_por_zona` |
| Q7 Promedio por tipo de sensor | `03` Q7 (`$arrayToObject`) · `q7_promedio_por_tipo` |
| Q8 Incidentes por categoría | `03` Q8 (Calle 30: heridos, túnel, firme mojado) · `q8_*` |
| Q9 Agregaciones | `03` Q9 (`$setWindowFields`: media móvil 3 meses y variación interanual con `$shift`) · `q9_evolucion_mensual` |
| Q10 Filtros múltiples | `03` Q10 (`$in`, rangos, `$expr` con `$hour` y `$dayOfWeek` en hora de Madrid) · `q10_*` |
| CRUD, filtros, operadores, proyecciones, ordenamiento | `02_crud.js` |
| Índices | `01_indices.js` y la evidencia en `04_experimentos.js` |
| KPIs del dashboard | `consultas.kpis`, `serie_temporal`, `distribucion_por_tipo`, `mapa_sensores`, `consumo_por_hora` |

**CO₂:** la red de Madrid no mide CO₂. Los indicadores de contaminación usan CO (monóxido de
carbono) y NO2.

## Resultados experimentales (2,13 M de lecturas, portátil con Docker Desktop)

**A. Índice vs. COLLSCAN**

| Consulta | Con índice | COLLSCAN | Documentos leídos |
|---|---:|---:|---:|
| Últimas 20 lecturas de un sensor | 0 ms | 1 030 ms | 20 vs. 2 133 362 |
| Lecturas de aire en una semana | 7 ms | 962 ms | 3 848 vs. 2 133 362 |
| Lecturas de un distrito en un mes | 9 ms | 836 ms | 6 504 vs. 2 133 362 |
| Alertas abiertas recientes (índice parcial) | 0 ms | 21 ms | 50 vs. 43 227 |

**B. Colección normal vs. time series** (mismo contenido)

| | Normal | Time series |
|---|---:|---:|
| storageSize | 117,3 MB | 6,5 MB |
| Índices | 138,4 MB | 0,2 MB |
| NO2 mensual por distrito | 1 060 ms | 363 ms |

La time series ocupa unas 18 veces menos y agrega unas 3 veces más rápido, pero tiene
restricciones en updates, deletes e índices secundarios. Por eso `lecturas` se mantiene como
colección normal, para poder demostrar todo el CRUD. En producción, las lecturas irían a una
time series y las alertas e incidentes a colecciones normales.

**C. Ingesta:** `insertOne` en bucle da unas 1 900 lecturas/s; `insertMany` en lotes de 5 000
da unas 55 000 lecturas/s, 29 veces más.

**Consultas lentas sobre la colección completa:** Q3 y Q7 recorren todas las variables de
2,1 M de documentos y tardan entre 9 y 13 s. En el dashboard se ejecutan con filtro de fechas
(un mes tarda menos de 1 s). Los tiempos dependen de la máquina; `04_experimentos.js` los vuelve
a medir.

## Integración con el resto del proyecto

- **PostgreSQL → MongoDB:** el importador lee de PostgreSQL qué sensores existen (por
  `sensores.codigo`), su posición, barrio y distrito, y los umbrales. Las lecturas de estaciones
  que no están en PostgreSQL se omiten y se informan.
- **Hadoop y Spark:** el histórico completo de tráfico (`data/raw/madrid/trafico`) y las
  lecturas de MongoDB son la entrada de HDFS y Spark; el índice `{ts: 1}` permite exportar por
  rango de fechas. Spark produce `mediciones_diarias` en PostgreSQL.
- **Fechas:** `ts` está en UTC. `consultas.py` recibe las fechas de los filtros en hora local de
  Madrid y las convierte; para mostrar resultados, `consultas.a_local(ts)`.
