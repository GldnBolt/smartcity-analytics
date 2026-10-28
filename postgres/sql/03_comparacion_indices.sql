-- SmartCity Madrid | PostgreSQL 16+
-- Ejecutar MANUALMENTE despues de cargar demanda_ruta; no en el init de Docker.
-- Dos EXPLAIN ANALYZE sobre los mismos datos y la misma consulta:
-- primero una copia temporal SIN indices, luego CON indice real.
-- No se borran ni deshabilitan indices de las tablas del proyecto.
-- La copia usa datos reales ya cargados; no genera ni inventa registros.
-- Presupuesto temporal: copia la demanda completa (aprox. 2,7M filas si se carga
-- ese dataset); requiere espacio temporal y tiempo proporcional a su volumen.

BEGIN;
SET LOCAL search_path = public, pg_catalog;

CREATE TEMP TABLE prueba_demanda_indices ON COMMIT DROP AS
SELECT id_ruta, fecha, hora, viajeros FROM public.demanda_ruta;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_temp.prueba_demanda_indices) THEN
        RAISE EXCEPTION 'Cargue demanda_ruta antes de comparar indices';
    END IF;
END;
$$;

-- Una linea existente y siete dias a partir de su primera fecha disponible.
CREATE TEMP TABLE parametros_indices ON COMMIT DROP AS
SELECT id_ruta, MIN(fecha) AS desde, MIN(fecha) + 7 AS hasta
FROM pg_temp.prueba_demanda_indices
GROUP BY id_ruta ORDER BY id_ruta LIMIT 1;

ANALYZE pg_temp.prueba_demanda_indices;
ANALYZE pg_temp.parametros_indices;

SELECT 'SIN INDICE' AS etapa, COUNT(*) AS filas_de_prueba
FROM pg_temp.prueba_demanda_indices;

EXPLAIN (ANALYZE, BUFFERS)
SELECT d.fecha, d.hora, d.viajeros
FROM pg_temp.prueba_demanda_indices d
WHERE d.id_ruta = (SELECT id_ruta FROM pg_temp.parametros_indices)
  AND d.fecha >= (SELECT desde FROM pg_temp.parametros_indices)
  AND d.fecha < (SELECT hasta FROM pg_temp.parametros_indices)
ORDER BY d.fecha, d.hora;

CREATE INDEX idx_prueba_demanda_ruta_fecha
ON prueba_demanda_indices(id_ruta, fecha);
ANALYZE pg_temp.prueba_demanda_indices;

SELECT 'CON INDICE' AS etapa, indexdef
FROM pg_indexes
WHERE schemaname LIKE 'pg_temp_%' AND tablename = 'prueba_demanda_indices';

EXPLAIN (ANALYZE, BUFFERS)
SELECT d.fecha, d.hora, d.viajeros
FROM pg_temp.prueba_demanda_indices d
WHERE d.id_ruta = (SELECT id_ruta FROM pg_temp.parametros_indices)
  AND d.fecha >= (SELECT desde FROM pg_temp.parametros_indices)
  AND d.fecha < (SELECT hasta FROM pg_temp.parametros_indices)
ORDER BY d.fecha, d.hora;

-- Leer Scan, filas, buffers y Execution Time. No se fuerza enable_seqscan=off.
-- Con pocos datos o poca selectividad puede seguir apareciendo Seq Scan.
-- La cache influye: repetir mediciones antes de atribuir toda mejora al indice.
-- Esta copia aisla el efecto del indice; la poda real se observa en Q22.
ROLLBACK;
