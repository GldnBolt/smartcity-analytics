# PostgreSQL — SmartCity Madrid

PostgreSQL almacena los datos estructurados y relacionales del proyecto y es la fuente de verdad
de los maestros:

- Territorio: distritos y barrios oficiales de Madrid.
- Sensores, tipos y categorías, variables medidas, umbrales y organismos responsables.
- Transporte público de la EMT: rutas, paradas, recorridos, flota y demanda de viajeros.
- Resúmenes diarios de las mediciones (`mediciones_diarias`), producidos por el procesamiento
  distribuido con Spark.

El importador de MongoDB lee los sensores, barrios, distritos y umbrales de esta base.

## Archivos

| Archivo | Función |
|---|---|
| [`sql/01_esquema.sql`](sql/01_esquema.sql) | DDL. Reinicia el esquema (destructivo) y crea tablas, particiones, índices, restricciones, triggers, vista, función, vista materializada y las tablas de staging. |
| [`sql/02_carga.sql`](sql/02_carga.sql) | DML de carga. Inserta los catálogos del proyecto y transforma las tablas de staging en el modelo con `INSERT ... SELECT`; al final vacía staging y actualiza la vista materializada y las estadísticas. |
| [`sql/03_consultas.sql`](sql/03_consultas.sql) | 22 consultas: las 10 obligatorias del enunciado y 12 adicionales (CTE, funciones de ventana, `HAVING`, agregados, fechas, `EXPLAIN ANALYZE`). |
| [`sql/04_comparacion_indices.sql`](sql/04_comparacion_indices.sql) | La misma consulta sobre la demanda sin índice y con índice. |
| [`cargador/extraer_staging.py`](cargador/extraer_staging.py) | Extracción: copia con `COPY` a `staging.*` las filas leídas de los archivos crudos. |
| [`cargador/maestros.py`](cargador/maestros.py) | Lee los barrios (TopoJSON) y selecciona los sensores de cada red, ubicándolos en su barrio por geometría. |
| [`cargador/transporte.py`](cargador/transporte.py) | Lee líneas, recorridos, paradas, flota y demanda de la EMT (CSV y ZIP); ubica cada parada. |

## Carga (ELT)

La carga sigue el patrón ELT: Python extrae y SQL transforma y carga.

1. **Extracción** (`extraer_staging.py`): Python hace solo lo que SQL no puede: abrir ZIP, leer CSV
   irregulares, decodificar el TopoJSON de los barrios, convertir coordenadas UTM y ubicar cada
   punto en su barrio. Copia las filas sin transformar a tablas `UNLOGGED` del esquema `staging`.
2. **Transformación y carga** (`02_carga.sql`): todo lo demás es DML, en una sola transacción:
   catálogos con `INSERT ... VALUES`, distritos derivados de los barrios, resolución de las FK
   por código, versión vigente del historial de la EMT (`DISTINCT ON`, funciones de ventana),
   limpieza de valores y consolidación de la demanda con `GROUP BY`. Verifica que ninguna fila
   de staging se haya perdido en un join y termina vaciando staging.

## Ejecución

El proyecto completo se recrea desde la raíz con `.\cargar_todo.ps1` o `sh cargar_todo.sh`, que
carga primero PostgreSQL y después MongoDB. Para trabajar solo con PostgreSQL:

```powershell
docker compose up -d --wait postgres
docker compose exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U smartcity -d smartcity -f /sql/01_esquema.sql
docker compose run --rm extractor_postgres
docker compose exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U smartcity -d smartcity -f /sql/02_carga.sql
docker compose exec -T postgres psql -X -U smartcity -d smartcity -f /sql/03_consultas.sql
docker compose exec -T postgres psql -X -U smartcity -d smartcity -f /sql/04_comparacion_indices.sql
```

Conexión local: `postgresql://smartcity:smartcity@localhost:5432/smartcity`.

`01_esquema.sql` también se ejecuta automáticamente la primera vez que se crea el volumen
`pg_data`. Para reinicializar una base existente se ejecuta con `psql`, como hace `cargar_todo`.
`02_carga.sql` se niega a ejecutarse si el modelo ya tiene datos o si staging está vacío.

## Modelo de datos

### Territorio

| Tabla | Contenido |
|---|---|
| `distritos` | 21 distritos municipales. `area` los clasifica en `Almendra central` (distritos 1 a 7, dentro de la M-30) o `Periferia`. |
| `ubicaciones` | 131 barrios oficiales. `codigo` es el código de barrio del geoportal (COD_BAR) y `latitud`/`longitud` su centroide. Cada barrio agrupa los sensores y paradas que contiene. |

### Sensores

| Tabla | Contenido |
|---|---|
| `categorias_sensor`, `tipos_sensor` | Clasificación de los sensores (Ambiente, Movilidad, Energía, Espacios públicos). |
| `sensores` | Cada sensor con su código de la red de origen, tipo, estado administrativo, barrio (`id_ubicacion`), organismo y posición exacta (`latitud`/`longitud`). `fuente_datos` indica si sus mediciones son reales o sintéticas. |
| `variables` | Magnitudes que mide cada tipo de sensor, con su unidad. |
| `umbrales` | Niveles de advertencia y crítico por variable y `periodo` de agregación (`horario`, `diario`, `mensual`), con la norma o criterio en `referencia`. |
| `organismos`, `usuarios` | Organismo responsable de cada red y su cuenta de contacto. |
| `mediciones_diarias` | Promedio, mínimo, máximo y número de lecturas por sensor, variable y día (hora local de Madrid). Particionada por año. |

El periodo de los umbrales es necesario porque un mismo contaminante tiene límites distintos según
la agregación: el NO2 tiene un límite horario de 200 µg/m³ y uno diario de 50 µg/m³. MongoDB
compara cada lectura con los umbrales de su periodo, y la consulta Q17 compara
`mediciones_diarias` con los de periodo `diario`.

### Transporte

| Tabla | Contenido |
|---|---|
| `rutas` | Líneas de autobús de la EMT, con origen y destino. |
| `paradas` | Paradas con coordenadas y barrio. `id_ubicacion` es NULL para las paradas fuera del término municipal. |
| `ruta_parada` | Recorrido de cada línea: parada por sentido y secuencia. |
| `vehiculos` | Catálogo de la flota (modelo, capacidad, combustible con el código de la EMT, alta y baja). La fuente no asigna vehículos a líneas, por lo que no existe esa relación. |
| `demanda_ruta` | Viajeros por línea, fecha y hora. Particionada por año. |

### Otros objetos

- **Particiones anuales** de `mediciones_diarias` y `demanda_ruta` para 2024–2027, creadas con
  `crear_particiones_anuales(desde, hasta)`. Insertar un año sin partición produce un error
  explícito; para otro rango: `SELECT public.crear_particiones_anuales(2020, 2023);`.
- **Triggers:** `trg_validar_tipo_medicion` impide registrar una medición de una variable que no
  corresponde al tipo del sensor; `trg_proteger_tipo_sensor` y `trg_proteger_tipo_variable`
  bloquean el cambio de tipo de sensores o variables que ya tienen mediciones.
- **Vista** `vw_resumen_sensores`: una fila por sensor con su tipo, categoría, barrio, distrito,
  área, organismo, posición y origen de datos.
- **Función** `obtener_sensores_por_estado(estado)`.
- **Vista materializada** `mv_demanda_ruta_mensual`: viajeros por línea y mes, con índice único
  para permitir `REFRESH ... CONCURRENTLY`. No se actualiza sola; `02_carga.sql` la refresca al
  terminar.

## Datos cargados (2024–2025)

| Tabla | Filas | Fuente |
|---|---:|---|
| distritos / ubicaciones | 21 / 131 | Geoportal municipal (límites oficiales) |
| sensores | 221 | Redes de aire, clima, ruido, tráfico y aparcamientos del Ayuntamiento, más 21 medidores de energía sintéticos (uno por distrito) |
| variables / umbrales | 40 / 15 | Definidos en [`02_carga.sql`](sql/02_carga.sql); umbrales con referencia normativa |
| rutas / paradas / ruta_parada | 323 / 4 938 / 12 019 | EMT (líneas, GTFS, paradas) |
| vehiculos | 5 351 | EMT (histórico de flota) |
| demanda_ruta | 2 712 608 | EMT (viajeros por línea, día y franja horaria) |
| **Total** | **≈ 2,74 M** | |

`mediciones_diarias` la llena el proceso de Spark a partir de los datos limpios. Debe insertar con
`ON CONFLICT (id_sensor, id_variable, fecha) DO UPDATE` para que una recarga no duplique filas.
Las consultas Q07, Q08, Q12, Q17 y Q19 dependen de esta tabla.

## Reglas de carga

- **Barrio por geometría:** cada sensor y cada parada se asigna al barrio cuyo polígono oficial
  contiene sus coordenadas (punto en polígono).
- **Organismos:** los datos abiertos no publican personas responsables. Cada organismo tiene una
  cuenta institucional con el dominio reservado `smartcity.example` (RFC 2606).
- **Estado de los sensores:** es administrativo. Los aparcamientos sin datos de ocupación en 2025
  son `inactivo`; el resto, `activo`. La ausencia de datos recientes la detecta Q19.
- **Fecha de instalación:** solo la publican las redes de aire y ruido; en el resto es NULL.
- **Líneas y flota:** se toma la versión vigente del historial publicado. En la flota, la
  capacidad 0 y las fechas de baja abiertas (9999) se cargan como NULL.
- **Recorridos:** por línea, la versión publicada con mayor vigencia. Las coordenadas salen del
  GTFS; las paradas que no están en él se ubican convirtiendo sus coordenadas UTM (ETRS89 30N).
- **Demanda:** la franja `Hnn` es la hora nn del día. La franja `Mnn` es la madrugada del servicio
  del día anterior: se asigna al día siguiente, hora nn, y se suma si coincide con una franja `H`.

## Comparación de índices

Resultado de [`04_comparacion_indices.sql`](sql/04_comparacion_indices.sql) sobre los datos
cargados:

| | Plan | Tiempo |
|---|---|---:|
| Sin índice | Seq Scan sobre 2 712 608 filas | 180 ms |
| Con índice `(id_ruta, fecha)` | Index Scan | 0,07 ms |

Los tiempos dependen de la máquina.

## Referencias

- PostgreSQL 16, particionamiento: https://www.postgresql.org/docs/16/ddl-partitioning.html
- PostgreSQL 16, vistas materializadas: https://www.postgresql.org/docs/16/sql-creatematerializedview.html
- PostgreSQL 16, EXPLAIN: https://www.postgresql.org/docs/16/sql-explain.html
