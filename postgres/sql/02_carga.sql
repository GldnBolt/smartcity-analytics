-- SmartCity Madrid | PostgreSQL 16+
-- Carga del modelo (la T y la L de ELT) con DML: INSERT con VALUES e
-- INSERT ... SELECT desde las tablas staging.*, que llena antes el extractor:
--   python -m postgres.cargador.extraer_staging
-- Requiere el esquema vacio (01_esquema.sql). Todo ocurre en una transaccion:
-- si una regla falla no queda una carga a medias. Al final vacia staging,
-- actualiza la vista materializada y las estadisticas.
-- psql -X -q -v ON_ERROR_STOP=1 -d smartcity -f 02_carga.sql
-- mediciones_diarias no se carga aqui: la produce Spark.

BEGIN;
SET LOCAL search_path = public, pg_catalog;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM sensores) OR EXISTS (SELECT 1 FROM rutas) THEN
        RAISE EXCEPTION 'El modelo ya tiene datos. Reinicializar con 01_esquema.sql.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM staging.sensores)
       OR NOT EXISTS (SELECT 1 FROM staging.demanda) THEN
        RAISE EXCEPTION 'staging esta vacio. Ejecutar antes el extractor (extraer_staging).';
    END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- 1. Catalogos definidos por el proyecto
-- ---------------------------------------------------------------------------

INSERT INTO categorias_sensor (nombre_categoria)
VALUES ('Ambiente'), ('Movilidad'), ('Energía'), ('Espacios públicos');

INSERT INTO tipos_sensor (tipo_sensor, id_categoria)
SELECT t.tipo_sensor, c.id_categoria
FROM (VALUES ('calidad_aire', 'Ambiente'), ('clima', 'Ambiente'), ('ruido', 'Ambiente'),
             ('trafico', 'Movilidad'), ('energia', 'Energía'),
             ('estacionamiento', 'Espacios públicos')) AS t (tipo_sensor, categoria)
JOIN categorias_sensor c ON c.nombre_categoria = t.categoria;

-- Los codigos de variable son los campos de mediciones en MongoDB.
INSERT INTO variables (tipo_sensor, codigo, unidad) VALUES
    ('calidad_aire', 'so2', 'µg/m³'), ('calidad_aire', 'no', 'µg/m³'),
    ('calidad_aire', 'no2', 'µg/m³'), ('calidad_aire', 'nox', 'µg/m³'),
    ('calidad_aire', 'pm25', 'µg/m³'), ('calidad_aire', 'pm10', 'µg/m³'),
    ('calidad_aire', 'o3', 'µg/m³'), ('calidad_aire', 'tolueno', 'µg/m³'),
    ('calidad_aire', 'benceno', 'µg/m³'), ('calidad_aire', 'etilbenceno', 'µg/m³'),
    ('calidad_aire', 'co', 'mg/m³'),
    ('clima', 'temperatura', '°C'), ('clima', 'humedad', '%'),
    ('clima', 'precipitacion', 'l/m²'), ('clima', 'viento_ms', 'm/s'),
    ('clima', 'viento_dir', 'grados'), ('clima', 'presion_hpa', 'hPa'),
    ('clima', 'radiacion_wm2', 'W/m²'),
    ('trafico', 'intensidad', 'vehículos/h'), ('trafico', 'ocupacion_pct', '%'),
    ('trafico', 'carga', '%'), ('trafico', 'indice_congestion', 'índice 0-1'),
    ('trafico', 'velocidad_prom', 'km/h'), ('trafico', 'tiempo_km_min', 'min/km'),
    ('ruido', 'laeq_dia', 'dB(A)'), ('ruido', 'laeq_tarde', 'dB(A)'),
    ('ruido', 'laeq_noche', 'dB(A)'), ('ruido', 'laeq_24h', 'dB(A)'),
    ('ruido', 'l1', 'dB(A)'), ('ruido', 'l10', 'dB(A)'), ('ruido', 'l50', 'dB(A)'),
    ('ruido', 'l90', 'dB(A)'), ('ruido', 'l99', 'dB(A)'),
    ('estacionamiento', 'ocupacion_pct', '%'), ('estacionamiento', 'ocupacion_9_21_pct', '%'),
    ('estacionamiento', 'vehiculos_dia', 'vehículos/día'), ('estacionamiento', 'plazas', 'plazas'),
    ('energia', 'consumo_kwh', 'kWh'), ('energia', 'demanda_kw', 'kW'),
    ('energia', 'temperatura_ref', '°C');

-- horario: MongoDB alerta sobre lecturas horarias; diario: se compara con
-- mediciones_diarias; mensual: datos mensuales de aparcamientos.
INSERT INTO umbrales (id_variable, periodo, advertencia, critico, referencia)
SELECT v.id, u.periodo, u.advertencia, u.critico, u.referencia
FROM (VALUES
    ('calidad_aire', 'no2', 'horario', 200, 400,
     'Directiva 2008/50/CE: valor límite horario 200 µg/m³; umbral de alerta 400 µg/m³'),
    ('calidad_aire', 'so2', 'horario', 350, 500,
     'Directiva 2008/50/CE: valor límite horario 350 µg/m³; umbral de alerta 500 µg/m³'),
    ('calidad_aire', 'o3', 'horario', 180, 240,
     'Directiva 2008/50/CE: umbral de información 180 µg/m³; umbral de alerta 240 µg/m³'),
    ('calidad_aire', 'pm10', 'diario', 45, 50,
     'OMS 2021: guía 24 h 45 µg/m³; Directiva 2008/50/CE: valor límite diario 50 µg/m³'),
    ('calidad_aire', 'pm25', 'diario', 15, 25,
     'OMS 2021: guía 24 h 15 µg/m³; Directiva (UE) 2024/2881: valor límite diario 25 µg/m³ (2030)'),
    ('calidad_aire', 'no2', 'diario', 25, 50,
     'OMS 2021: guía 24 h 25 µg/m³; Directiva (UE) 2024/2881: valor límite diario 50 µg/m³ (2030)'),
    ('calidad_aire', 'so2', 'diario', 40, 125,
     'OMS 2021: guía 24 h 40 µg/m³; Directiva 2008/50/CE: valor límite diario 125 µg/m³'),
    ('clima', 'temperatura', 'horario', 36, 39,
     'Criterio del proyecto, aproximado a los avisos por calor de AEMET en Madrid'),
    ('clima', 'precipitacion', 'horario', 15, 30,
     'Criterio del proyecto, aproximado a los avisos por lluvia de AEMET'),
    ('ruido', 'laeq_dia', 'diario', 65, 70,
     'RD 1367/2007: objetivo de calidad acústica Ld 65 dB(A) en uso residencial; crítico = objetivo + 5 dB (criterio del proyecto)'),
    ('ruido', 'laeq_tarde', 'diario', 65, 70,
     'RD 1367/2007: objetivo de calidad acústica Le 65 dB(A) en uso residencial; crítico = objetivo + 5 dB (criterio del proyecto)'),
    ('ruido', 'laeq_noche', 'diario', 55, 60,
     'RD 1367/2007: objetivo de calidad acústica Ln 55 dB(A) en uso residencial; crítico = objetivo + 5 dB (criterio del proyecto)'),
    ('trafico', 'carga', 'horario', 70, 90,
     'Criterio del proyecto: carga = grado de saturación de la vía (%)'),
    ('energia', 'consumo_kwh', 'horario', 1900, 2200,
     'Criterio del proyecto (consumo sintético)'),
    ('estacionamiento', 'ocupacion_pct', 'mensual', 85, 95,
     'Criterio del proyecto')
) AS u (tipo_sensor, variable, periodo, advertencia, critico, referencia)
JOIN variables v ON v.tipo_sensor = u.tipo_sensor AND v.codigo = u.variable;

-- Organismo responsable de cada red de sensores. Los datos abiertos no
-- publican personas: cada organismo tiene una cuenta institucional con el
-- dominio reservado smartcity.example (RFC 2606).
CREATE TEMP TABLE redes (orden, red, organismo, responsable, correo) ON COMMIT DROP AS
VALUES
    (1, 'aire', 'Ayuntamiento de Madrid - Sistema Integral de la Calidad del Aire',
     'Servicio de Calidad del Aire', 'calidad.aire@smartcity.example'),
    (2, 'movilidad', 'Ayuntamiento de Madrid - Centro de Gestión de Movilidad',
     'Centro de Gestión de Movilidad', 'movilidad@smartcity.example'),
    (3, 'ruido', 'Ayuntamiento de Madrid - Red de Vigilancia Acústica',
     'Servicio de Contaminación Acústica', 'ruido@smartcity.example'),
    (4, 'calle30', 'Madrid Calle 30', 'Centro de Control de Calle 30',
     'calle30@smartcity.example'),
    (5, 'meteo', 'Ayuntamiento de Madrid - Red Meteorológica Municipal',
     'Red Meteorológica Municipal', 'meteorologia@smartcity.example'),
    (6, 'parking', 'Ayuntamiento de Madrid - Aparcamientos municipales',
     'Gestión de Aparcamientos', 'aparcamientos@smartcity.example'),
    (7, 'simulacion', 'SmartCity - Modelos sintéticos (sin dato abierto)',
     'Equipo del proyecto SmartCity', 'proyecto@smartcity.example');

INSERT INTO usuarios (nombre, correo)
SELECT 'Responsable - ' || responsable, correo FROM redes ORDER BY orden;

INSERT INTO organismos (nombre, responsable)
SELECT r.organismo, u.id_usuario
FROM redes r JOIN usuarios u ON u.correo = r.correo
ORDER BY r.orden;

-- ---------------------------------------------------------------------------
-- 2. Territorio: los distritos se derivan de los barrios
-- ---------------------------------------------------------------------------

-- Almendra central: distritos 1 a 7, dentro de la M-30.
INSERT INTO distritos (codigo, nombre, area)
SELECT DISTINCT 'D' || lpad(cod_distrito::TEXT, 2, '0'), distrito,
       CASE WHEN cod_distrito BETWEEN 1 AND 7 THEN 'Almendra central' ELSE 'Periferia' END
FROM staging.barrios
ORDER BY 1;

INSERT INTO ubicaciones (codigo, nombre_zona, id_distrito, latitud, longitud)
SELECT b.codigo, b.nombre, d.id, b.latitud, b.longitud
FROM staging.barrios b
JOIN distritos d ON d.codigo = 'D' || lpad(b.cod_distrito::TEXT, 2, '0')
ORDER BY b.codigo::INTEGER;

-- ---------------------------------------------------------------------------
-- 3. Sensores: barrio y organismo se resuelven por codigo
-- ---------------------------------------------------------------------------

INSERT INTO sensores (codigo, nombre, tipo_sensor, fecha_instalacion, estado,
                      id_ubicacion, id_organismo, latitud, longitud, fuente_datos)
SELECT s.codigo, s.nombre, s.tipo_sensor, s.fecha_instalacion, s.estado,
       u.id_ubicacion, o.id_organismo, s.latitud, s.longitud, s.fuente_datos
FROM staging.sensores s
JOIN ubicaciones u ON u.codigo = s.cod_barrio
JOIN redes r ON r.red = s.red
JOIN organismos o ON o.nombre = r.organismo
ORDER BY s.codigo;

-- ---------------------------------------------------------------------------
-- 4. Transporte publico de la EMT
-- ---------------------------------------------------------------------------

-- Version vigente de cada linea (la de inicio mas reciente). El nombre es el
-- comercial del GTFS (las circulares lo publican como "Circular 1 -"); si la
-- linea no esta en el GTFS, se compone con origen y destino.
WITH vigente AS (
    SELECT DISTINCT ON (linea) linea, btrim(origen) AS origen, btrim(destino) AS destino
    FROM staging.lineas
    ORDER BY linea, fecha_ini DESC
)
INSERT INTO rutas (codigo_linea, nombre, origen, destino)
SELECT v.linea,
       left(COALESCE(NULLIF(rtrim(btrim(g.nombre), ' -'), ''),
                     concat_ws(' - ', initcap(NULLIF(v.origen, '')),
                                      initcap(NULLIF(v.destino, '')))), 150),
       left(NULLIF(v.origen, ''), 200),
       left(NULLIF(v.destino, ''), 200)
FROM vigente v
LEFT JOIN staging.gtfs_rutas g ON g.route_id = v.linea
ORDER BY v.linea;

-- Recorrido de cada linea: la version publicada con mayor vigencia (fecha de
-- fin mas lejana). Una posicion (linea, sentido, secuencia) repetida conserva
-- la primera fila del archivo.
CREATE TEMP TABLE recorrido_vigente ON COMMIT DROP AS
WITH version AS (
    SELECT DISTINCT ON (linea) linea, left(fecha_fin, 10) AS fin, fecha_ini
    FROM staging.recorridos
    WHERE linea IN (SELECT codigo_linea FROM rutas)
    ORDER BY linea, left(fecha_fin, 10) DESC, fecha_ini DESC
)
SELECT DISTINCT ON (r.linea, r.sentido, r.secuencia) r.linea, r.parada, r.sentido, r.secuencia
FROM staging.recorridos r
JOIN version v ON v.linea = r.linea AND v.fin = left(r.fecha_fin, 10)
              AND v.fecha_ini = r.fecha_ini
WHERE r.secuencia >= 1
ORDER BY r.linea, r.sentido, r.secuencia, r.n;

-- Solo las paradas que forman parte de algun recorrido vigente. Las que caen
-- fuera del termino municipal no tienen barrio (id_ubicacion NULL).
INSERT INTO paradas (codigo, nombre, lat, lon, id_ubicacion)
SELECT p.codigo, left(p.nombre, 200), p.lat, p.lon, u.id_ubicacion
FROM staging.paradas p
LEFT JOIN ubicaciones u ON u.codigo = p.cod_barrio
WHERE p.codigo IN (SELECT parada FROM recorrido_vigente)
ORDER BY p.codigo;

INSERT INTO ruta_parada (id_ruta, id_parada, sentido, secuencia)
SELECT ru.id, pa.id, r.sentido, r.secuencia
FROM recorrido_vigente r
JOIN rutas ru ON ru.codigo_linea = r.linea
JOIN paradas pa ON pa.codigo = r.parada;

-- Flota: datos de la version vigente de cada autobus; el alta es la primera
-- version publicada. Capacidad 0 y fechas de baja abiertas (2999, 9999) -> NULL.
WITH versiones AS (
    SELECT btrim(vehiculo) AS vehiculo, marca, modelo, combustible, capacidad, fecha_fin,
           MIN(left(fecha_ini, 10)) OVER w AS primera,
           row_number() OVER (w ORDER BY fecha_ini DESC) AS rn
    FROM staging.flota
    WINDOW w AS (PARTITION BY btrim(vehiculo))
), vigente AS (
    SELECT vehiculo,
           NULLIF(regexp_replace(btrim(btrim(marca) || ' ' || btrim(modelo)), '\s+', ' ', 'g'), '')
               AS modelo,
           NULLIF(btrim(combustible), '') AS combustible,
           CASE WHEN btrim(capacidad) ~ '^\d+$' THEN btrim(capacidad)::INTEGER END AS capacidad,
           primera::DATE AS alta,
           CASE WHEN fecha_fin ~ '^\d{4}-\d{2}-\d{2}' AND left(fecha_fin, 4) < '2999'
                THEN left(fecha_fin, 10)::DATE END AS baja
    FROM versiones
    WHERE rn = 1
)
INSERT INTO vehiculos (codigo, modelo, capacidad, combustible, fecha_alta, fecha_baja)
SELECT vehiculo, modelo, NULLIF(capacidad, 0), combustible, alta,
       CASE WHEN baja >= alta THEN baja END
FROM vigente
ORDER BY vehiculo;

-- Demanda: la franja Hnn es la hora nn del dia. La franja Mnn es la madrugada
-- del servicio del dia anterior: pasa al dia siguiente, hora nn, y se suma si
-- coincide con una franja H. Se cargan 2024 y 2025 completos.
INSERT INTO demanda_ruta (id_ruta, fecha, hora, viajeros)
SELECT r.id, x.fecha, x.hora, SUM(x.viajeros)
FROM (SELECT linea, viajeros,
             CASE WHEN left(franja, 1) = 'M' THEN fecha + 1 ELSE fecha END AS fecha,
             substr(franja, 2)::SMALLINT AS hora
      FROM staging.demanda) x
JOIN rutas r ON r.codigo_linea = x.linea
WHERE x.fecha BETWEEN DATE '2024-01-01' AND DATE '2025-12-31'
GROUP BY r.id, x.fecha, x.hora;

-- ---------------------------------------------------------------------------
-- 5. Verificacion y resumen
-- ---------------------------------------------------------------------------

-- Un JOIN sin pareja descartaria filas en silencio: se exige que no falte ninguna.
DO $$
BEGIN
    IF (SELECT COUNT(*) FROM sensores) <> (SELECT COUNT(*) FROM staging.sensores) THEN
        RAISE EXCEPTION 'Hay sensores en staging sin barrio u organismo valido';
    END IF;
    IF (SELECT COUNT(*) FROM ubicaciones) <> (SELECT COUNT(*) FROM staging.barrios) THEN
        RAISE EXCEPTION 'Hay barrios en staging sin distrito';
    END IF;
END;
$$;

SELECT tabla, filas FROM (VALUES
    (1, 'categorias_sensor', (SELECT COUNT(*) FROM categorias_sensor)),
    (2, 'tipos_sensor', (SELECT COUNT(*) FROM tipos_sensor)),
    (3, 'variables', (SELECT COUNT(*) FROM variables)),
    (4, 'umbrales', (SELECT COUNT(*) FROM umbrales)),
    (5, 'usuarios', (SELECT COUNT(*) FROM usuarios)),
    (6, 'organismos', (SELECT COUNT(*) FROM organismos)),
    (7, 'distritos', (SELECT COUNT(*) FROM distritos)),
    (8, 'ubicaciones', (SELECT COUNT(*) FROM ubicaciones)),
    (9, 'sensores', (SELECT COUNT(*) FROM sensores)),
    (10, 'rutas', (SELECT COUNT(*) FROM rutas)),
    (11, 'paradas', (SELECT COUNT(*) FROM paradas)),
    (12, 'ruta_parada', (SELECT COUNT(*) FROM ruta_parada)),
    (13, 'vehiculos', (SELECT COUNT(*) FROM vehiculos)),
    (14, 'demanda_ruta', (SELECT COUNT(*) FROM demanda_ruta))
) AS t (orden, tabla, filas)
ORDER BY orden;

SELECT (SELECT COUNT(*) FROM staging.paradas p JOIN paradas q ON q.codigo = p.codigo
        WHERE p.desde_utm) AS paradas_con_utm_convertidas,
       (SELECT COUNT(*) FROM paradas WHERE id_ubicacion IS NULL) AS paradas_fuera_de_madrid,
       (SELECT COUNT(DISTINCT linea) FROM staging.demanda d
        WHERE NOT EXISTS (SELECT 1 FROM rutas r WHERE r.codigo_linea = d.linea))
           AS lineas_demanda_sin_ficha;

TRUNCATE staging.barrios, staging.sensores, staging.lineas, staging.gtfs_rutas,
         staging.recorridos, staging.paradas, staging.flota, staging.demanda;

REFRESH MATERIALIZED VIEW mv_demanda_ruta_mensual;

COMMIT;

ANALYZE;
