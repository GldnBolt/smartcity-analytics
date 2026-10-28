-- SmartCity Madrid | PostgreSQL 16+
-- Ejecutar despues de 01_esquema.sql y de la carga de datos.
-- Este archivo solo consulta. Vistas y funciones se crean en 01_esquema.sql.
-- Antes de consultar la vista materializada, actualizarla tras la carga:
-- REFRESH MATERIALIZED VIEW public.mv_demanda_ruta_mensual;
-- Los periodos de ejemplo son [2024-01-01, 2026-01-01), salvo donde se indica.
-- En la interfaz, reemplazar los parametros por parametros del driver SQL.
SET search_path = public, pg_catalog;

-- Q01. Sensores por tipo, incluidos tipos sin sensores.
-- No unir variables aqui: multiplicaria los sensores por sus variables.
SELECT ts.tipo_sensor, COUNT(s.id_sensor) AS cantidad_sensores
FROM tipos_sensor ts
LEFT JOIN sensores s ON s.tipo_sensor = ts.tipo_sensor
GROUP BY ts.tipo_sensor
ORDER BY cantidad_sensores DESC, ts.tipo_sensor;

-- Q02. Sensores por ubicacion; la identidad es id_ubicacion, no el nombre.
SELECT u.id_ubicacion, u.nombre_zona, d.codigo AS codigo_distrito,
       d.nombre AS distrito, COUNT(s.id_sensor) AS cantidad_sensores
FROM ubicaciones u
JOIN distritos d ON d.id = u.id_distrito
LEFT JOIN sensores s ON s.id_ubicacion = u.id_ubicacion
GROUP BY u.id_ubicacion, u.nombre_zona, d.codigo, d.nombre
ORDER BY cantidad_sensores DESC, u.id_ubicacion;

-- Q03. Estados administrativos; no equivalen a conectividad en tiempo real.
SELECT COUNT(*) FILTER (WHERE estado = 'activo') AS sensores_activos,
       COUNT(*) FILTER (WHERE estado = 'inactivo') AS sensores_inactivos,
       COUNT(*) AS total_sensores
FROM sensores;

-- Q04. Sensores por organismo responsable.
SELECT o.id_organismo, o.nombre AS nombre_organismo,
       us.id_usuario, us.nombre AS nombre_responsable,
       us.correo AS correo_responsable, COUNT(s.id_sensor) AS cantidad_sensores
FROM organismos o
JOIN usuarios us ON us.id_usuario = o.responsable
LEFT JOIN sensores s ON s.id_organismo = o.id_organismo
GROUP BY o.id_organismo, o.nombre, us.id_usuario, us.nombre, us.correo
ORDER BY cantidad_sensores DESC, o.id_organismo;

-- Q05. Ubicaciones con mas sensores; RANK conserva empates.
SELECT RANK() OVER (ORDER BY COUNT(s.id_sensor) DESC) AS ranking,
       u.id_ubicacion, u.nombre_zona, d.nombre AS distrito,
       COUNT(s.id_sensor) AS cantidad_sensores
FROM ubicaciones u
JOIN distritos d ON d.id = u.id_distrito
LEFT JOIN sensores s ON s.id_ubicacion = u.id_ubicacion
GROUP BY u.id_ubicacion, u.nombre_zona, d.nombre
ORDER BY cantidad_sensores DESC, u.id_ubicacion;

-- Q06. Instalados por anio; no se inventa un anio para las fechas desconocidas.
SELECT EXTRACT(YEAR FROM fecha_instalacion)::INTEGER AS anio,
       CASE WHEN fecha_instalacion IS NULL THEN 'Sin fecha publicada'
            ELSE EXTRACT(YEAR FROM fecha_instalacion)::INTEGER::TEXT END AS periodo,
       COUNT(*) AS cantidad_sensores
FROM sensores
GROUP BY EXTRACT(YEAR FROM fecha_instalacion)::INTEGER,
         CASE WHEN fecha_instalacion IS NULL THEN 'Sin fecha publicada'
              ELSE EXTRACT(YEAR FROM fecha_instalacion)::INTEGER::TEXT END
ORDER BY anio NULLS LAST;

-- Q07. Sensores que nunca tienen un resumen diario en PostgreSQL.
-- Su alcance depende de que la carga de resumenes este completa.
SELECT s.id_sensor, s.codigo, s.nombre, s.tipo_sensor, s.fecha_instalacion,
       s.estado, u.nombre_zona, d.nombre AS distrito
FROM sensores s
JOIN ubicaciones u ON u.id_ubicacion = s.id_ubicacion
JOIN distritos d ON d.id = u.id_distrito
WHERE NOT EXISTS (
    SELECT 1 FROM mediciones_diarias m WHERE m.id_sensor = s.id_sensor
)
ORDER BY s.nombre, s.id_sensor;

-- Q08. Ranking de ubicaciones monitoreadas: CTE + ventana + SUM.
-- n_lecturas cuenta observaciones escalares por variable, no documentos Mongo.
-- Incluye ubicaciones sin actividad con conteos cero.
WITH actividad AS (
    SELECT s.id_ubicacion, COUNT(DISTINCT s.id_sensor) AS sensores_con_actividad,
           COUNT(*) AS resumenes_diarios, SUM(m.n_lecturas) AS observaciones
    FROM sensores s
    JOIN mediciones_diarias m ON m.id_sensor = s.id_sensor
    GROUP BY s.id_ubicacion
)
SELECT RANK() OVER (ORDER BY COALESCE(a.observaciones, 0) DESC) AS ranking,
       u.id_ubicacion, u.nombre_zona, d.nombre AS distrito,
       COALESCE(a.sensores_con_actividad, 0) AS sensores_con_actividad,
       COALESCE(a.resumenes_diarios, 0) AS resumenes_diarios,
       COALESCE(a.observaciones, 0) AS observaciones
FROM ubicaciones u
JOIN distritos d ON d.id = u.id_distrito
LEFT JOIN actividad a ON a.id_ubicacion = u.id_ubicacion
ORDER BY observaciones DESC, u.id_ubicacion;

-- Q09. Consulta de la vista normal: una fila por sensor.
SELECT * FROM vw_resumen_sensores
ORDER BY codigo_distrito, id_ubicacion, nombre_sensor, id_sensor;

-- Q10. Consulta mediante funcion almacenada.
SELECT * FROM obtener_sensores_por_estado('activo');

-- Q11. Consulta de la vista materializada, actualizada despues de la carga.
SELECT r.codigo_linea, r.nombre, mv.mes, mv.viajeros_totales,
       mv.horas_registradas, mv.promedio_por_hora_registrada
FROM mv_demanda_ruta_mensual mv
JOIN rutas r ON r.id = mv.id_ruta
WHERE mv.mes >= DATE '2024-01-01' AND mv.mes < DATE '2026-01-01'
ORDER BY mv.mes, mv.viajeros_totales DESC, r.id;

-- Q12. Estadisticas por distrito y variable. No mezcla magnitudes ni unidades.
-- AVG(promedio) pesa igual cada sensor-dia; el otro promedio pesa cada lectura.
-- HAVING exige al menos dos sensores distintos con datos de esa variable.
SELECT d.id AS id_distrito, d.nombre AS distrito,
       v.id AS id_variable, v.tipo_sensor, v.codigo AS variable, v.unidad,
       COUNT(DISTINCT m.id_sensor) AS sensores_con_datos,
       COUNT(*) AS resumenes_diarios, SUM(m.n_lecturas) AS lecturas_validas,
       AVG(m.promedio) AS media_de_promedios_diarios,
       SUM(m.promedio * m.n_lecturas) / SUM(m.n_lecturas) AS promedio_ponderado,
       MIN(m.minimo) AS minimo_registrado, MAX(m.maximo) AS maximo_registrado
FROM mediciones_diarias m
JOIN variables v ON v.id = m.id_variable
JOIN sensores s ON s.id_sensor = m.id_sensor
JOIN ubicaciones u ON u.id_ubicacion = s.id_ubicacion
JOIN distritos d ON d.id = u.id_distrito
WHERE m.fecha >= DATE '2024-01-01' AND m.fecha < DATE '2026-01-01'
GROUP BY d.id, d.nombre, v.id, v.tipo_sensor, v.codigo, v.unidad
HAVING COUNT(DISTINCT m.id_sensor) >= 2
ORDER BY d.id, v.id;

-- Q13. Rutas con mayor utilizacion en el periodo; incluye las que no tienen datos.
-- NULL viajeros indica ausencia de registros; cero indica viajeros observados=0.
WITH totales AS (
    SELECT id_ruta, SUM(viajeros) AS viajeros, COUNT(*) AS horas_registradas
    FROM demanda_ruta
    WHERE fecha >= DATE '2024-01-01' AND fecha < DATE '2026-01-01'
    GROUP BY id_ruta
)
SELECT RANK() OVER (ORDER BY t.viajeros DESC NULLS LAST) AS ranking,
       r.id AS id_ruta, r.codigo_linea, r.nombre, r.origen, r.destino,
       t.viajeros, COALESCE(t.horas_registradas, 0) AS horas_registradas
FROM rutas r LEFT JOIN totales t ON t.id_ruta = r.id
ORDER BY t.viajeros DESC NULLS LAST, r.id;

-- Q14. Hora pico de viajeros por linea durante 2025; devuelve todos los empates.
-- Mide demanda de viajeros, no velocidad ni congestion vehicular.
WITH por_hora AS (
    SELECT id_ruta, hora, SUM(viajeros) AS viajeros,
           COUNT(*) AS dias_con_datos, AVG(viajeros) AS promedio_por_dia_observado
    FROM demanda_ruta
    WHERE fecha >= DATE '2025-01-01' AND fecha < DATE '2026-01-01'
    GROUP BY id_ruta, hora
), posiciones AS (
    SELECT *, RANK() OVER (PARTITION BY id_ruta ORDER BY viajeros DESC) AS puesto
    FROM por_hora
)
SELECT r.codigo_linea, r.nombre, p.hora, p.viajeros,
       p.dias_con_datos, p.promedio_por_dia_observado
FROM posiciones p JOIN rutas r ON r.id = p.id_ruta
WHERE p.puesto = 1
ORDER BY r.id, p.hora;

-- Q15. Comparacion 2024 vs 2025; informa cobertura para no ocultar anios parciales.
WITH comparacion AS (
    SELECT id_ruta,
           SUM(viajeros) FILTER (WHERE fecha < DATE '2025-01-01') AS viajeros_2024,
           SUM(viajeros) FILTER (WHERE fecha >= DATE '2025-01-01') AS viajeros_2025,
           COUNT(*) FILTER (WHERE fecha < DATE '2025-01-01') AS horas_2024,
           COUNT(*) FILTER (WHERE fecha >= DATE '2025-01-01') AS horas_2025,
           COUNT(DISTINCT fecha) FILTER (WHERE fecha < DATE '2025-01-01') AS dias_2024,
           COUNT(DISTINCT fecha) FILTER (WHERE fecha >= DATE '2025-01-01') AS dias_2025
    FROM demanda_ruta
    WHERE fecha >= DATE '2024-01-01' AND fecha < DATE '2026-01-01'
    GROUP BY id_ruta
)
SELECT r.codigo_linea, r.nombre, c.viajeros_2024, c.viajeros_2025,
       c.horas_2024, c.horas_2025, c.dias_2024, c.dias_2025,
       c.viajeros_2025 - c.viajeros_2024 AS diferencia,
       ROUND(100.0 * (c.viajeros_2025 - c.viajeros_2024)
             / NULLIF(c.viajeros_2024, 0), 2) AS variacion_porcentual
FROM comparacion c JOIN rutas r ON r.id = c.id_ruta
ORDER BY r.id;

-- Q16. Recorridos ordenados por linea, sentido y secuencia.
SELECT r.codigo_linea, r.nombre AS ruta, rp.sentido, rp.secuencia,
       p.codigo AS codigo_parada, p.nombre AS parada, p.lat, p.lon,
       u.nombre_zona, d.nombre AS distrito
FROM ruta_parada rp
JOIN rutas r ON r.id = rp.id_ruta
JOIN paradas p ON p.id = rp.id_parada
LEFT JOIN ubicaciones u ON u.id_ubicacion = p.id_ubicacion
LEFT JOIN distritos d ON d.id = u.id_distrito
ORDER BY r.codigo_linea, rp.sentido, rp.secuencia;

-- Q17. Promedios diarios por encima de los umbrales de periodo diario.
-- Los umbrales horarios (los usa MongoDB) no se comparan con promedios diarios.
SELECT s.codigo AS sensor, v.codigo AS variable, v.unidad,
       m.fecha, m.promedio, t.advertencia, t.critico, t.referencia,
       CASE WHEN m.promedio >= t.critico THEN 'critico'
            ELSE 'advertencia' END AS nivel
FROM mediciones_diarias m
JOIN sensores s ON s.id_sensor = m.id_sensor
JOIN variables v ON v.id = m.id_variable
JOIN umbrales t ON t.id_variable = m.id_variable AND t.periodo = 'diario'
WHERE m.fecha >= DATE '2024-01-01' AND m.fecha < DATE '2026-01-01'
  AND (m.promedio >= t.advertencia OR m.promedio >= t.critico)
ORDER BY m.fecha DESC, s.codigo, v.id;

-- Q18. DISTINCT explicito: distritos con sensores activos.
SELECT DISTINCT d.id, d.codigo, d.nombre
FROM distritos d
JOIN ubicaciones u ON u.id_distrito = d.id
JOIN sensores s ON s.id_ubicacion = u.id_ubicacion
WHERE s.estado = 'activo'
ORDER BY d.codigo;

-- Q19. Sensores activos sin datos recientes, usando la ultima fecha del dataset.
-- Para fuentes con frecuencias distintas, adaptar tolerancia_dias por tipo.
WITH parametros AS (
    SELECT MAX(fecha) AS fecha_referencia, 7 AS tolerancia_dias
    FROM mediciones_diarias
), ultimas AS (
    SELECT id_sensor, MAX(fecha) AS ultima_fecha
    FROM mediciones_diarias GROUP BY id_sensor
)
SELECT s.codigo, s.nombre, s.tipo_sensor, u.ultima_fecha,
       p.fecha_referencia, p.fecha_referencia - u.ultima_fecha AS dias_sin_datos
FROM sensores s
LEFT JOIN ultimas u ON u.id_sensor = s.id_sensor
CROSS JOIN parametros p
WHERE s.estado = 'activo'
  AND (u.ultima_fecha IS NULL
       OR u.ultima_fecha < p.fecha_referencia - p.tolerancia_dias)
ORDER BY u.ultima_fecha NULLS FIRST, s.codigo;

-- Q20. Catalogo de flota, sin inventar asignaciones de vehiculos a rutas.
SELECT combustible, COUNT(*) AS vehiculos,
       COUNT(*) FILTER (WHERE fecha_baja IS NULL) AS sin_fecha_baja,
       AVG(capacidad) AS capacidad_media_conocida,
       MIN(fecha_alta) AS primera_alta, MAX(fecha_alta) AS ultima_alta
FROM vehiculos GROUP BY combustible ORDER BY vehiculos DESC, combustible;

-- Q21. Evidencia de particiones anuales, incluso si estan vacias.
SELECT parent.relname AS tabla, child.relname AS particion,
       pg_get_expr(child.relpartbound, child.oid) AS limites
FROM pg_inherits i
JOIN pg_class parent ON parent.oid = i.inhparent
JOIN pg_class child ON child.oid = i.inhrelid
WHERE parent.oid IN ('public.mediciones_diarias'::regclass,
                     'public.demanda_ruta'::regclass)
ORDER BY tabla, particion;

-- Q22. Plan real sobre la tabla particionada y su PK (id_ruta, fecha, hora).
-- Elegir una linea existente y un rango selectivo permite observar indice/poda.
EXPLAIN (ANALYZE, BUFFERS)
SELECT fecha, hora, viajeros FROM demanda_ruta
WHERE id_ruta = (SELECT MIN(id) FROM rutas)
  AND fecha >= DATE '2025-01-01' AND fecha < DATE '2025-02-01'
ORDER BY fecha, hora;

-- La comparacion fisica SIN indice / CON indice esta en 04_comparacion_indices.sql.
