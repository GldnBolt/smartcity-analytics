-- SmartCity Madrid | PostgreSQL 16+
-- ATENCION: reinicializacion DESTRUCTIVA. Borra las tablas del proyecto y sus datos.
-- Ejecutar en la base dedicada al proyecto, antes de cargar los datos reales.
-- psql -X -v ON_ERROR_STOP=1 -d smartcity -f 01_esquema.sql
-- Los identificadores internos se generan; los codigos externos se cargan en codigo.

BEGIN;
SET LOCAL search_path = public, pg_catalog;

DROP MATERIALIZED VIEW IF EXISTS mv_demanda_ruta_mensual;
DROP VIEW IF EXISTS vw_resumen_sensores;
DROP FUNCTION IF EXISTS obtener_sensores_por_estado(VARCHAR);
DROP FUNCTION IF EXISTS crear_particiones_anuales(INTEGER, INTEGER);
-- Se eliminan juntos para resolver sus dependencias internas; sin CASCADE a
-- objetos ajenos. Una dependencia externa desconocida abortara la transaccion.
DROP TABLE IF EXISTS mediciones_diarias, demanda_ruta, ruta_parada, paradas,
    vehiculos, lecturas, umbrales, variables, sensores, organismos,
    tipos_sensor, rutas, usuarios, ubicaciones, distritos, categorias_sensor;
DROP FUNCTION IF EXISTS validar_tipo_medicion();
DROP FUNCTION IF EXISTS proteger_tipo_con_mediciones();
-- staging solo contiene tablas de paso de este proyecto.
DROP SCHEMA IF EXISTS staging CASCADE;

CREATE TABLE categorias_sensor (
    id_categoria BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre_categoria VARCHAR(80) NOT NULL UNIQUE,
    CHECK (btrim(nombre_categoria) <> '')
);

CREATE TABLE distritos (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo VARCHAR(10) NOT NULL UNIQUE,
    nombre VARCHAR(100) NOT NULL,
    area VARCHAR(30) NOT NULL CHECK (area IN ('Almendra central', 'Periferia')),
    CHECK (btrim(codigo) <> ''),
    CHECK (btrim(nombre) <> '')
);
COMMENT ON COLUMN distritos.area IS
    'Almendra central: distritos dentro de la M-30 (1 a 7). Periferia: el resto.';

-- Cada ubicacion es un barrio municipal oficial (131): zona que agrupa sensores y paradas.
CREATE TABLE ubicaciones (
    id_ubicacion BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo VARCHAR(10) NOT NULL UNIQUE CHECK (btrim(codigo) <> ''),
    nombre_zona VARCHAR(100) NOT NULL CHECK (btrim(nombre_zona) <> ''),
    id_distrito BIGINT NOT NULL REFERENCES distritos(id) ON DELETE RESTRICT,
    latitud NUMERIC(9,6) NOT NULL CHECK (latitud BETWEEN -90 AND 90),
    longitud NUMERIC(9,6) NOT NULL CHECK (longitud BETWEEN -180 AND 180)
);
COMMENT ON TABLE ubicaciones IS
    'Barrios municipales de Madrid (geoportal). codigo = COD_BAR oficial; latitud/longitud = centroide.';

CREATE TABLE usuarios (
    id_usuario BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre VARCHAR(120) NOT NULL CHECK (btrim(nombre) <> ''),
    correo VARCHAR(254) NOT NULL UNIQUE CHECK (btrim(correo) <> '')
);

CREATE TABLE tipos_sensor (
    tipo_sensor VARCHAR(40) PRIMARY KEY CHECK (btrim(tipo_sensor) <> ''),
    id_categoria BIGINT NOT NULL
        REFERENCES categorias_sensor(id_categoria) ON DELETE RESTRICT
    -- La unidad pertenece a variables, no al tipo de sensor.
);

CREATE TABLE organismos (
    id_organismo BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre VARCHAR(120) NOT NULL CHECK (btrim(nombre) <> ''),
    responsable BIGINT NOT NULL REFERENCES usuarios(id_usuario) ON DELETE RESTRICT
);

CREATE TABLE sensores (
    id_sensor BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo VARCHAR(20) NOT NULL UNIQUE CHECK (btrim(codigo) <> ''),
    nombre VARCHAR(100) NOT NULL CHECK (btrim(nombre) <> ''),
    tipo_sensor VARCHAR(40) NOT NULL
        REFERENCES tipos_sensor(tipo_sensor) ON DELETE RESTRICT,
    fecha_instalacion DATE NULL CHECK (isfinite(fecha_instalacion)),
    estado VARCHAR(8) NOT NULL CHECK (estado IN ('activo', 'inactivo')),
    id_ubicacion BIGINT NOT NULL
        REFERENCES ubicaciones(id_ubicacion) ON DELETE RESTRICT,
    id_organismo BIGINT NOT NULL
        REFERENCES organismos(id_organismo) ON DELETE RESTRICT,
    latitud NUMERIC(9,6) NOT NULL CHECK (latitud BETWEEN -90 AND 90),
    longitud NUMERIC(9,6) NOT NULL CHECK (longitud BETWEEN -180 AND 180),
    fuente_datos VARCHAR(10) NOT NULL DEFAULT 'real'
        CHECK (fuente_datos IN ('real', 'sintetico'))
);
COMMENT ON COLUMN sensores.latitud IS
    'Posicion exacta del sensor; id_ubicacion es el barrio que la contiene.';
COMMENT ON COLUMN sensores.fuente_datos IS
    'sintetico: el sensor no tiene dato abierto y sus mediciones se modelan (energia).';

CREATE TABLE variables (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo_sensor VARCHAR(40) NOT NULL
        REFERENCES tipos_sensor(tipo_sensor) ON DELETE RESTRICT,
    codigo VARCHAR(30) NOT NULL CHECK (btrim(codigo) <> ''),
    unidad VARCHAR(30) NOT NULL CHECK (btrim(unidad) <> ''),
    UNIQUE (tipo_sensor, codigo)
);

CREATE TABLE umbrales (
    id_variable BIGINT NOT NULL REFERENCES variables(id) ON DELETE RESTRICT,
    periodo VARCHAR(10) NOT NULL CHECK (periodo IN ('horario', 'diario', 'mensual')),
    advertencia NUMERIC(18,6),
    critico NUMERIC(18,6),
    referencia TEXT NOT NULL CHECK (btrim(referencia) <> ''),
    PRIMARY KEY (id_variable, periodo),
    CHECK (advertencia IS NOT NULL OR critico IS NOT NULL),
    CHECK (advertencia <> 'NaN'::NUMERIC AND critico <> 'NaN'::NUMERIC),
    CHECK (advertencia <= critico)
);
COMMENT ON TABLE umbrales IS
    'Umbrales superiores por variable y periodo de agregacion, en la unidad de variables. horario: lo usa MongoDB para alertas sobre lecturas horarias; diario: se compara con mediciones_diarias; mensual: datos mensuales. referencia indica la norma o el criterio. NULL significa nivel no definido.';

CREATE TABLE mediciones_diarias (
    id_sensor BIGINT NOT NULL REFERENCES sensores(id_sensor) ON DELETE RESTRICT,
    id_variable BIGINT NOT NULL REFERENCES variables(id) ON DELETE RESTRICT,
    fecha DATE NOT NULL CHECK (isfinite(fecha)),
    promedio NUMERIC(18,6) NOT NULL,
    minimo NUMERIC(18,6) NOT NULL,
    maximo NUMERIC(18,6) NOT NULL,
    n_lecturas INTEGER NOT NULL CHECK (n_lecturas > 0),
    PRIMARY KEY (id_sensor, id_variable, fecha),
    CHECK (promedio <> 'NaN'::NUMERIC AND minimo <> 'NaN'::NUMERIC
           AND maximo <> 'NaN'::NUMERIC),
    CHECK (minimo <= promedio AND promedio <= maximo)
) PARTITION BY RANGE (fecha);
COMMENT ON TABLE mediciones_diarias IS
    'Una fila por sensor, variable y fecha local Europe/Madrid; resume observaciones validas. Lecturas originales en MongoDB/HDFS. La PK evita duplicar un resumen al recargar.';

CREATE TABLE rutas (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo_linea VARCHAR(20) NOT NULL UNIQUE CHECK (btrim(codigo_linea) <> ''),
    nombre VARCHAR(150) NOT NULL CHECK (btrim(nombre) <> ''),
    origen VARCHAR(200),
    destino VARCHAR(200)
);

CREATE TABLE paradas (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo VARCHAR(20) NOT NULL UNIQUE CHECK (btrim(codigo) <> ''),
    nombre VARCHAR(200) NOT NULL CHECK (btrim(nombre) <> ''),
    lat NUMERIC(9,6) NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon NUMERIC(9,6) NOT NULL CHECK (lon BETWEEN -180 AND 180),
    id_ubicacion BIGINT REFERENCES ubicaciones(id_ubicacion) ON DELETE RESTRICT
);
COMMENT ON COLUMN paradas.id_ubicacion IS
    'Puede ser NULL si la fuente no permite asignar aun una zona. No inventar coordenadas ni distritos.';

CREATE TABLE ruta_parada (
    id_ruta BIGINT NOT NULL REFERENCES rutas(id) ON DELETE RESTRICT,
    id_parada BIGINT NOT NULL REFERENCES paradas(id) ON DELETE RESTRICT,
    sentido SMALLINT NOT NULL CHECK (sentido IN (1, 2)),
    secuencia INTEGER NOT NULL CHECK (secuencia >= 1),
    PRIMARY KEY (id_ruta, sentido, secuencia)
    -- Una parada puede repetirse en un recorrido circular en distinta secuencia.
);

CREATE TABLE vehiculos (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo VARCHAR(30) NOT NULL UNIQUE CHECK (btrim(codigo) <> ''),
    modelo VARCHAR(120),
    capacidad INTEGER CHECK (capacidad > 0),
    combustible VARCHAR(60),
    fecha_alta DATE CHECK (isfinite(fecha_alta)),
    fecha_baja DATE CHECK (isfinite(fecha_baja)),
    CHECK (fecha_baja >= fecha_alta)
);
-- La fuente de demanda no asigna necesariamente un vehiculo a una linea/viaje.
-- No se inventa esa relacion: vehiculos representa el catalogo de flota.

CREATE TABLE demanda_ruta (
    id_ruta BIGINT NOT NULL REFERENCES rutas(id) ON DELETE RESTRICT,
    fecha DATE NOT NULL CHECK (isfinite(fecha)),
    hora SMALLINT NOT NULL CHECK (hora BETWEEN 0 AND 23),
    viajeros BIGINT NOT NULL CHECK (viajeros >= 0),
    PRIMARY KEY (id_ruta, fecha, hora)
) PARTITION BY RANGE (fecha);
COMMENT ON TABLE demanda_ruta IS
    'Viajeros agregados por linea, fecha local Europe/Madrid y hora 0..23. Consolidar sentidos, paradas u horas repetidas por cambio horario antes de insertar.';

-- Las PK de tablas particionadas incluyen fecha, la clave de particion.
-- Crear el rango ANTES de cargar datos; no se envian fechas desconocidas a
-- una particion DEFAULT que oculte la falta de preparacion de un anio.
CREATE FUNCTION crear_particiones_anuales(p_desde INTEGER, p_hasta INTEGER)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    anio INTEGER;
    tabla TEXT;
BEGIN
    IF p_desde IS NULL OR p_hasta IS NULL OR p_desde < 1
       OR p_hasta > 9998 OR p_desde > p_hasta THEN
        RAISE EXCEPTION 'Rango de anios invalido: % a %', p_desde, p_hasta;
    END IF;
    FOREACH tabla IN ARRAY ARRAY['mediciones_diarias', 'demanda_ruta'] LOOP
        FOR anio IN p_desde..p_hasta LOOP
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS public.%I PARTITION OF public.%I FOR VALUES FROM (%L) TO (%L)',
                tabla || '_' || anio, tabla,
                make_date(anio, 1, 1), make_date(anio + 1, 1, 1));
        END LOOP;
    END LOOP;
END;
$$;
SELECT crear_particiones_anuales(2024, 2027);

-- Indices de FK y filtros. Las PK/UNIQUE ya cubren: umbrales(id_variable, periodo),
-- variables.tipo_sensor, ruta_parada.id_ruta, mediciones_diarias.id_sensor
-- y demanda_ruta(id_ruta, fecha). Evitar indices identicos redundantes.
CREATE INDEX idx_ubicaciones_distrito ON ubicaciones(id_distrito);
CREATE INDEX idx_tipos_sensor_categoria ON tipos_sensor(id_categoria);
CREATE INDEX idx_organismos_responsable ON organismos(responsable);
CREATE INDEX idx_sensores_tipo ON sensores(tipo_sensor);
CREATE INDEX idx_sensores_ubicacion ON sensores(id_ubicacion);
CREATE INDEX idx_sensores_organismo ON sensores(id_organismo);
CREATE INDEX idx_mediciones_sensor_fecha ON mediciones_diarias(id_sensor, fecha);
CREATE INDEX idx_mediciones_variable_fecha ON mediciones_diarias(id_variable, fecha);
CREATE INDEX idx_paradas_ubicacion ON paradas(id_ubicacion);
CREATE INDEX idx_ruta_parada_parada ON ruta_parada(id_parada);
CREATE INDEX idx_demanda_fecha_hora ON demanda_ruta(fecha, hora);

-- Una variable de aire no puede asociarse por accidente a un sensor de ruido.
CREATE FUNCTION validar_tipo_medicion() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    tipo_del_sensor TEXT;
    tipo_de_variable TEXT;
BEGIN
    SELECT s.tipo_sensor INTO tipo_del_sensor
    FROM public.sensores s WHERE s.id_sensor = NEW.id_sensor FOR SHARE;
    SELECT v.tipo_sensor INTO tipo_de_variable
    FROM public.variables v WHERE v.id = NEW.id_variable FOR SHARE;
    -- Las FK producen el error si alguno de los identificadores no existe.
    IF tipo_del_sensor IS NOT NULL AND tipo_de_variable IS NOT NULL
       AND tipo_del_sensor <> tipo_de_variable THEN
        RAISE EXCEPTION 'El sensor % y la variable % tienen tipos distintos',
            NEW.id_sensor, NEW.id_variable USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_validar_tipo_medicion
    BEFORE INSERT OR UPDATE OF id_sensor, id_variable ON mediciones_diarias
    FOR EACH ROW EXECUTE FUNCTION validar_tipo_medicion();

CREATE FUNCTION proteger_tipo_con_mediciones() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tipo_sensor IS DISTINCT FROM OLD.tipo_sensor THEN
        IF TG_TABLE_NAME = 'sensores' THEN
            IF EXISTS (SELECT 1 FROM public.mediciones_diarias
                       WHERE id_sensor = OLD.id_sensor) THEN
                RAISE EXCEPTION 'No se puede cambiar el tipo de un sensor con mediciones';
            END IF;
        ELSE
            IF EXISTS (SELECT 1 FROM public.mediciones_diarias
                       WHERE id_variable = OLD.id) THEN
                RAISE EXCEPTION 'No se puede cambiar el tipo de una variable con mediciones';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_proteger_tipo_sensor BEFORE UPDATE OF tipo_sensor ON sensores
    FOR EACH ROW EXECUTE FUNCTION proteger_tipo_con_mediciones();
CREATE TRIGGER trg_proteger_tipo_variable BEFORE UPDATE OF tipo_sensor ON variables
    FOR EACH ROW EXECUTE FUNCTION proteger_tipo_con_mediciones();

CREATE VIEW vw_resumen_sensores AS
SELECT s.id_sensor, s.codigo, s.nombre AS nombre_sensor, s.tipo_sensor,
       c.id_categoria, c.nombre_categoria, s.fecha_instalacion,
       EXTRACT(YEAR FROM s.fecha_instalacion)::INTEGER AS anio_instalacion,
       s.estado, s.fuente_datos, s.latitud, s.longitud,
       u.id_ubicacion, u.codigo AS codigo_barrio, u.nombre_zona,
       d.id AS id_distrito, d.codigo AS codigo_distrito, d.nombre AS distrito, d.area,
       o.id_organismo, o.nombre AS nombre_organismo,
       us.nombre AS nombre_responsable, us.correo AS correo_responsable
FROM sensores s
JOIN tipos_sensor ts ON ts.tipo_sensor = s.tipo_sensor
JOIN categorias_sensor c ON c.id_categoria = ts.id_categoria
JOIN ubicaciones u ON u.id_ubicacion = s.id_ubicacion
JOIN distritos d ON d.id = u.id_distrito
JOIN organismos o ON o.id_organismo = s.id_organismo
JOIN usuarios us ON us.id_usuario = o.responsable;

CREATE FUNCTION obtener_sensores_por_estado(p_estado VARCHAR)
RETURNS TABLE (id_sensor BIGINT, codigo VARCHAR, nombre VARCHAR,
               tipo_sensor VARCHAR, fecha_instalacion DATE, estado VARCHAR)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_estado TEXT := lower(btrim(p_estado));
BEGIN
    IF v_estado IS NULL OR v_estado NOT IN ('activo', 'inactivo') THEN
        RAISE EXCEPTION 'Estado no valido. Use activo o inactivo.';
    END IF;
    RETURN QUERY
    SELECT s.id_sensor, s.codigo, s.nombre, s.tipo_sensor,
           s.fecha_instalacion, s.estado
    FROM public.sensores s WHERE s.estado = v_estado
    ORDER BY s.nombre, s.id_sensor;
END;
$$;

CREATE MATERIALIZED VIEW mv_demanda_ruta_mensual AS
SELECT id_ruta, date_trunc('month', fecha::TIMESTAMP)::DATE AS mes,
       SUM(viajeros) AS viajeros_totales,
       COUNT(*) AS horas_registradas,
       AVG(viajeros) AS promedio_por_hora_registrada
FROM demanda_ruta
GROUP BY id_ruta, date_trunc('month', fecha::TIMESTAMP)::DATE
WITH DATA;
CREATE UNIQUE INDEX ux_mv_demanda_ruta_mes ON mv_demanda_ruta_mensual(id_ruta, mes);
-- Tras cada carga: REFRESH MATERIALIZED VIEW public.mv_demanda_ruta_mensual;
-- ANALYZE public.mediciones_diarias; ANALYZE public.demanda_ruta;

-- Area de staging (ELT). El extractor de Python copia aqui las filas leidas de
-- los archivos crudos (ZIP, TopoJSON, CSV) sin transformarlas, salvo la
-- geometria: barrio de cada punto y coordenadas UTM convertidas. 02_carga.sql
-- las transforma y carga en el modelo con DML y al terminar vacia staging.
-- UNLOGGED: son datos de paso que se pueden regenerar, no necesitan WAL.
CREATE SCHEMA staging;

-- Barrios del geoportal; los distritos se derivan de aqui.
CREATE UNLOGGED TABLE staging.barrios (
    codigo TEXT, nombre TEXT, cod_distrito INTEGER, distrito TEXT,
    latitud NUMERIC(9,6), longitud NUMERIC(9,6)
);
-- Sensores seleccionados de cada red; red identifica al organismo responsable.
CREATE UNLOGGED TABLE staging.sensores (
    codigo TEXT, nombre TEXT, tipo_sensor TEXT, red TEXT, fecha_instalacion DATE,
    estado TEXT, cod_barrio TEXT, latitud NUMERIC(9,6), longitud NUMERIC(9,6),
    fuente_datos TEXT
);
-- Historial de lineas de la EMT, tal como se publica (una fila por version).
CREATE UNLOGGED TABLE staging.lineas (
    linea TEXT, fecha_ini TEXT, fecha_fin TEXT, origen TEXT, destino TEXT
);
-- routes.txt del GTFS: nombre comercial de cada linea.
CREATE UNLOGGED TABLE staging.gtfs_rutas (route_id TEXT, nombre TEXT);
-- Paradas por linea, sentido y secuencia, de todas las versiones publicadas.
-- n conserva el orden del archivo para resolver duplicados de forma estable.
CREATE UNLOGGED TABLE staging.recorridos (
    n BIGINT GENERATED ALWAYS AS IDENTITY,
    linea TEXT, fecha_ini TEXT, fecha_fin TEXT, parada TEXT,
    sentido INTEGER, secuencia INTEGER
);
-- Una fila por parada: coordenadas WGS84 (GTFS o UTM convertidas) y barrio.
CREATE UNLOGGED TABLE staging.paradas (
    codigo TEXT, nombre TEXT, lat NUMERIC(9,6), lon NUMERIC(9,6),
    cod_barrio TEXT, desde_utm BOOLEAN
);
-- Historial de la flota (una fila por version de cada autobus).
CREATE UNLOGGED TABLE staging.flota (
    vehiculo TEXT, fecha_ini TEXT, fecha_fin TEXT, marca TEXT, modelo TEXT,
    combustible TEXT, capacidad TEXT
);
-- Viajeros por linea, fecha y franja (Hnn / Mnn) tal como los publica la EMT.
CREATE UNLOGGED TABLE staging.demanda (
    fecha DATE, linea TEXT, franja TEXT, viajeros BIGINT
);

COMMIT;
