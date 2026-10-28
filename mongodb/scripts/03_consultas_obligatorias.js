// =============================================================================
// 03_consultas_obligatorias.js — Las 10 consultas obligatorias + preguntas del enunciado
//
//   docker compose exec mongo mongosh /scripts/03_consultas_obligatorias.js
//
// Datos: redes reales del Ayuntamiento de Madrid (aire, clima, tráfico, ruido, aparcamientos,
// incidentes de Calle 30), 2024–2025. La energía es sintética (ver README).
// Notas:
//  - `ts` se guarda en UTC; toda agrupación por hora/día usa timezone "Europe/Madrid"
//    (con horario de verano).
//  - Los valores que la red marcó como NO validados están en meta.no_validados, fuera de
//    `mediciones`, así que las estadísticas no los incluyen.
//  - Los mismos pipelines existen en Python (mongodb/consultas.py) para el dashboard.
// =============================================================================
db = db.getSiblingDB("smartcity");

const TZ = "Europe/Madrid";
const SENSOR = "AIR-008";                       // Escuelas Aguirre: la estación que más contaminantes mide
const DESDE = ISODate("2025-11-30T23:00:00Z");  // 1-dic-2025 00:00 hora de Madrid
const HASTA = ISODate("2025-12-07T23:00:00Z");  // 8-dic-2025 00:00 hora de Madrid
const ANIO_2025 = { $gte: ISODate("2024-12-31T23:00:00Z"), $lt: ISODate("2025-12-31T23:00:00Z") };

function ejecutar(titulo, fn) {
  print(`\n========== ${titulo} ==========`);
  const t = Date.now();
  const res = fn();
  const arr = Array.isArray(res) ? res : res.toArray ? res.toArray() : [res];
  printjson(arr);
  print(`→ ${arr.length} resultado(s) en ${Date.now() - t} ms`);
}

// -----------------------------------------------------------------------------
// 1. Lecturas de un sensor específico
//    Usa el índice {sensor_id:1, ts:-1}: igualdad + orden ya resuelto por el índice.
// -----------------------------------------------------------------------------
ejecutar(`Q1. Últimas 10 lecturas del sensor ${SENSOR}`, () =>
  db.lecturas.find({ sensor_id: SENSOR }, { _id: 0, ts: 1, mediciones: 1, "meta.no_validados": 1 })
    .sort({ ts: -1 }).limit(10));

// -----------------------------------------------------------------------------
// 2. Lecturas en un rango de fechas (resumen diario de la primera semana de diciembre 2025)
// -----------------------------------------------------------------------------
ejecutar("Q2. Lecturas por día y tipo entre 2025-12-01 y 2025-12-07", () =>
  db.lecturas.aggregate([
    { $match: { ts: { $gte: DESDE, $lt: HASTA } } },
    { $group: {
        _id: { dia: { $dateToString: { format: "%Y-%m-%d", date: "$ts", timezone: TZ } }, tipo: "$tipo" },
        lecturas: { $sum: 1 } } },
    { $group: { _id: "$_id.dia", total: { $sum: "$lecturas" },
                por_tipo: { $push: { k: "$_id.tipo", v: "$lecturas" } } } },
    { $project: { _id: 0, dia: "$_id", total: 1, por_tipo: { $arrayToObject: "$por_tipo" } } },
    { $sort: { dia: 1 } },
  ]));

// -----------------------------------------------------------------------------
// 3. Valores máximos y mínimos registrados
//    $objectToArray convierte `mediciones` (campos distintos por tipo y por estación) en pares
//    {k,v}: UNA sola consulta calcula máx/mín de TODAS las variables sin conocer el esquema.
//    $top devuelve además cuándo y dónde se registró el máximo.
// -----------------------------------------------------------------------------
ejecutar("Q3. Máximo y mínimo histórico de cada variable (por tipo de sensor)", () =>
  db.lecturas.aggregate([
    { $project: { tipo: 1, ts: 1, sensor_id: 1, m: { $objectToArray: "$mediciones" } } },
    { $unwind: "$m" },
    { $match: { "m.v": { $type: "number" } } },
    { $group: { _id: { tipo: "$tipo", variable: "$m.k" },
                maximo: { $top: { sortBy: { "m.v": -1 }, output: { valor: "$m.v", sensor: "$sensor_id", ts: "$ts" } } },
                minimo: { $min: "$m.v" }, lecturas: { $sum: 1 } } },
    { $sort: { "_id.tipo": 1, "_id.variable": 1 } },
  ], { allowDiskUse: true }));

// -----------------------------------------------------------------------------
// 4. Eventos críticos detectados — $facet: tres vistas del mismo conjunto en una pasada
// -----------------------------------------------------------------------------
ejecutar("Q4. Eventos de severidad alta/crítica", () =>
  db.eventos.aggregate([
    { $match: { severidad: { $in: ["alta", "critica"] } } },
    { $facet: {
        por_tipo: [{ $group: { _id: "$tipo_evento", total: { $sum: 1 } } }, { $sort: { total: -1 } }],
        por_zona: [{ $group: { _id: "$ubicacion.zona", total: { $sum: 1 } } }, { $sort: { total: -1 } }, { $limit: 5 }],
        ultimos: [{ $sort: { ts: -1 } }, { $limit: 5 },
                  { $project: { _id: 0, ts: 1, tipo_evento: 1, severidad: 1, sensor_id: 1, "ubicacion.zona": 1, payload: 1 } }],
    } },
  ]));

// -----------------------------------------------------------------------------
// 5. Alertas emitidas por zona geográfica
//    5a: agrupación por distrito con conteo condicional por nivel.
//    5b: geoespacial real — alertas en un radio de 3 km de la Puerta del Sol ($geoNear + 2dsphere).
// -----------------------------------------------------------------------------
ejecutar("Q5a. Alertas por distrito y nivel", () =>
  db.alertas.aggregate([
    { $group: {
        _id: { zona: "$ubicacion.zona", area: "$ubicacion.area" },
        total: { $sum: 1 },
        criticas: { $sum: { $cond: [{ $eq: ["$nivel", "critica"] }, 1, 0] } },
        advertencias: { $sum: { $cond: [{ $eq: ["$nivel", "advertencia"] }, 1, 0] } },
        abiertas: { $sum: { $cond: [{ $eq: ["$estado", "abierta"] }, 1, 0] } },
        variables: { $addToSet: "$variable" } } },
    { $sort: { total: -1 } },
  ]));

ejecutar("Q5b. Alertas críticas a menos de 3 km de la Puerta del Sol, por anillos de distancia", () =>
  db.alertas.aggregate([
    { $geoNear: { near: { type: "Point", coordinates: [-3.7038, 40.4168] }, distanceField: "distancia_m",
                  maxDistance: 3000, query: { nivel: "critica" }, spherical: true } },
    { $bucket: { groupBy: "$distancia_m", boundaries: [0, 1000, 2000, 3001], default: "otro",
                 output: { alertas: { $sum: 1 }, zonas: { $addToSet: "$ubicacion.zona" },
                           variables: { $addToSet: "$variable" } } } },
  ]));

// -----------------------------------------------------------------------------
// 6. Promedio de mediciones por zona
//    $avg ignora los documentos que no tienen el campo: se mezclan lecturas de aire, clima,
//    ruido y tráfico en el mismo $group sin columnas nulas.
// -----------------------------------------------------------------------------
ejecutar("Q6. Promedios por distrito (2025)", () =>
  db.lecturas.aggregate([
    { $match: { tipo: { $in: ["calidad_aire", "clima", "ruido", "trafico"] }, ts: ANIO_2025 } },
    { $group: {
        _id: "$ubicacion.zona",
        no2: { $avg: "$mediciones.no2" }, pm10: { $avg: "$mediciones.pm10" }, pm25: { $avg: "$mediciones.pm25" },
        o3: { $avg: "$mediciones.o3" }, temperatura: { $avg: "$mediciones.temperatura" },
        humedad: { $avg: "$mediciones.humedad" }, laeq_dia: { $avg: "$mediciones.laeq_dia" },
        laeq_noche: { $avg: "$mediciones.laeq_noche" }, intensidad: { $avg: "$mediciones.intensidad" },
        lecturas: { $sum: 1 } } },
    { $project: { zona: "$_id", _id: 0, lecturas: 1,
                  no2: { $round: ["$no2", 1] }, pm10: { $round: ["$pm10", 1] }, pm25: { $round: ["$pm25", 1] },
                  o3: { $round: ["$o3", 1] }, temperatura: { $round: ["$temperatura", 1] },
                  humedad: { $round: ["$humedad", 1] }, laeq_dia: { $round: ["$laeq_dia", 1] },
                  laeq_noche: { $round: ["$laeq_noche", 1] }, intensidad: { $round: ["$intensidad", 0] } } },
    { $sort: { no2: -1 } },
  ]));

// -----------------------------------------------------------------------------
// 7. Promedio por tipo de sensor — cada tipo devuelve SUS variables ($objectToArray/$arrayToObject)
// -----------------------------------------------------------------------------
ejecutar("Q7. Promedio de cada variable por tipo de sensor", () =>
  db.lecturas.aggregate([
    { $project: { tipo: 1, m: { $objectToArray: "$mediciones" } } },
    { $unwind: "$m" },
    { $match: { "m.v": { $type: "number" } } },
    { $group: { _id: { tipo: "$tipo", variable: "$m.k" }, promedio: { $avg: "$m.v" }, n: { $sum: 1 } } },
    { $group: { _id: "$_id.tipo", lecturas: { $max: "$n" },
                promedios: { $push: { k: "$_id.variable", v: { $round: ["$promedio", 2] } } } } },
    { $project: { _id: 0, tipo: "$_id", lecturas: 1, promedios: { $arrayToObject: "$promedios" } } },
    { $sort: { tipo: 1 } },
  ], { allowDiskUse: true }));

// -----------------------------------------------------------------------------
// 8. Incidentes registrados por categoría (accidentes reales de Calle 30)
//    `detalle` solo contiene lo que ocurrió en cada incidente: $sum ignora los campos ausentes.
// -----------------------------------------------------------------------------
ejecutar("Q8. Incidentes de Calle 30 por categoría", () =>
  db.incidentes.aggregate([
    { $group: {
        _id: "$categoria",
        total: { $sum: 1 },
        heridos_leves: { $sum: "$detalle.heridos_leves" },
        heridos_graves: { $sum: "$detalle.heridos_graves" },
        victimas_mortales: { $sum: "$detalle.victimas_mortales" },
        en_tunel: { $sum: { $cond: [{ $in: ["tunel", { $ifNull: ["$etiquetas", []] }] }, 1, 0] } },
        firme_mojado: { $sum: { $cond: [{ $in: ["firme_mojado", { $ifNull: ["$etiquetas", []] }] }, 1, 0] } },
        vias: { $addToSet: "$ubicacion.via" } } },
    { $project: { categoria: "$_id", _id: 0, total: 1, heridos_leves: 1, heridos_graves: 1, victimas_mortales: 1,
                  pct_tunel: { $round: [{ $multiply: [{ $divide: ["$en_tunel", "$total"] }, 100] }, 1] },
                  pct_firme_mojado: { $round: [{ $multiply: [{ $divide: ["$firme_mojado", "$total"] }, 100] }, 1] },
                  vias: 1 } },
    { $sort: { total: -1 } },
  ]));

// -----------------------------------------------------------------------------
// 9. Consulta con agregaciones avanzadas — evolución mensual de NO2 por distrito con media
//    móvil de 3 meses y variación interanual ($setWindowFields: $avg con ventana y $shift).
// -----------------------------------------------------------------------------
ejecutar("Q9. Evolución mensual de NO2 — Salamanca vs. Retiro (media móvil 3 meses e interanual)", () =>
  db.lecturas.aggregate([
    { $match: { tipo: "calidad_aire", "ubicacion.zona": { $in: ["Salamanca", "Retiro"] },
                "mediciones.no2": { $exists: true } } },
    { $group: { _id: { zona: "$ubicacion.zona",
                       mes: { $dateTrunc: { date: "$ts", unit: "month", timezone: TZ } } },
                no2: { $avg: "$mediciones.no2" } } },
    { $setWindowFields: {
        partitionBy: "$_id.zona",
        sortBy: { "_id.mes": 1 },
        output: {
          media_movil_3m: { $avg: "$no2", window: { documents: [-2, 0] } },
          mismo_mes_anio_anterior: { $shift: { output: "$no2", by: -12 } },
        } } },
    { $project: { _id: 0, zona: "$_id.zona",
                  mes: { $dateToString: { format: "%Y-%m", date: "$_id.mes", timezone: TZ } },
                  no2: { $round: ["$no2", 1] },
                  media_movil_3m: { $round: ["$media_movil_3m", 1] },
                  variacion_interanual_pct: { $round: [{ $multiply: [{ $divide: [
                    { $subtract: ["$no2", "$mismo_mes_anio_anterior"] }, "$mismo_mes_anio_anterior"] }, 100] }, 1] } } },
    { $sort: { zona: 1, mes: 1 } },
  ]));

// -----------------------------------------------------------------------------
// 10. Consulta con filtros múltiples — saturación del tráfico en hora pico de días laborales:
//     igualdad, $in, rangos numéricos y de fecha, y $expr con funciones de fecha en hora local.
// -----------------------------------------------------------------------------
ejecutar("Q10. Saturación (carga ≥ 70 % y ocupación ≥ 20 %) en hora pico laboral, 2025", () =>
  db.lecturas.aggregate([
    { $match: {
        tipo: "trafico",
        "ubicacion.zona": { $in: ["Fuencarral - El Pardo", "Carabanchel", "Chamartín", "Centro"] },
        ts: ANIO_2025,
        "mediciones.carga": { $gte: 70 },
        "mediciones.ocupacion_pct": { $gte: 20 },
        $expr: { $and: [
          { $in: [{ $hour: { date: "$ts", timezone: TZ } }, [7, 8, 9, 17, 18, 19, 20]] },
          { $not: { $in: [{ $dayOfWeek: { date: "$ts", timezone: TZ } }, [1, 7]] } },  // sin domingo(1) ni sábado(7)
        ] } } },
    { $group: { _id: { zona: "$ubicacion.zona", hora: { $hour: { date: "$ts", timezone: TZ } } },
                horas_saturadas: { $sum: 1 }, carga_prom: { $avg: "$mediciones.carga" },
                intensidad_prom: { $avg: "$mediciones.intensidad" }, puntos: { $addToSet: "$sensor_id" } } },
    { $project: { _id: 0, zona: "$_id.zona", hora: "$_id.hora", horas_saturadas: 1,
                  carga_prom: { $round: ["$carga_prom", 1] }, intensidad_prom: { $round: ["$intensidad_prom", 0] },
                  puntos: { $size: "$puntos" } } },
    { $sort: { zona: 1, hora: 1 } },
  ]));

// =============================================================================
// Preguntas de ejemplo del enunciado
// =============================================================================
ejecutar("P1. ¿Cuál es la temperatura promedio por zona? (2025)", () =>
  db.lecturas.aggregate([
    { $match: { tipo: "clima", "mediciones.temperatura": { $exists: true }, ts: ANIO_2025 } },
    { $group: { _id: "$ubicacion.zona", temperaturaPromedio: { $avg: "$mediciones.temperatura" },
                maxima: { $max: "$mediciones.temperatura" }, minima: { $min: "$mediciones.temperatura" } } },
    { $project: { zona: "$_id", _id: 0, temperaturaPromedio: { $round: ["$temperaturaPromedio", 1] }, maxima: 1, minima: 1 } },
    { $sort: { temperaturaPromedio: -1 } },
  ]));

ejecutar("P2. ¿Qué sensores registraron valores superiores al límite establecido? (top 10)", () =>
  db.alertas.aggregate([
    { $group: { _id: { sensor: "$sensor_id", variable: "$variable" }, veces: { $sum: 1 },
                criticas: { $sum: { $cond: [{ $eq: ["$nivel", "critica"] }, 1, 0] } },
                valor_maximo: { $max: "$valor" }, umbral: { $min: "$umbral" }, zona: { $first: "$ubicacion.zona" } } },
    { $sort: { veces: -1 } },
    { $limit: 10 },
  ]));

ejecutar("P3. ¿Cuántas mediciones produjo cada sensor? (top 10 y bottom 5)", () =>
  db.lecturas.aggregate([
    { $group: { _id: "$sensor_id", mediciones: { $sum: 1 }, tipo: { $first: "$tipo" },
                primera: { $min: "$ts" }, ultima: { $max: "$ts" } } },
    { $sort: { mediciones: -1 } },
    { $facet: { mas_activos: [{ $limit: 10 }], menos_activos: [{ $sort: { mediciones: 1 } }, { $limit: 5 }] } },
  ]));

ejecutar("P4. ¿Qué zonas presentan mayor contaminación? (NO2 medio, p95 y % de horas sobre 40 µg/m³)", () =>
  db.lecturas.aggregate([
    { $match: { tipo: "calidad_aire", "mediciones.no2": { $exists: true } } },
    { $group: { _id: "$ubicacion.zona", no2_prom: { $avg: "$mediciones.no2" },
                no2_p95: { $percentile: { input: "$mediciones.no2", p: [0.95], method: "approximate" } },
                sobre_40: { $sum: { $cond: [{ $gt: ["$mediciones.no2", 40] }, 1, 0] } }, n: { $sum: 1 } } },
    { $project: { _id: 0, zona: "$_id", no2_prom: { $round: ["$no2_prom", 1] },
                  no2_p95: { $round: [{ $arrayElemAt: ["$no2_p95", 0] }, 1] },
                  pct_horas_sobre_40: { $round: [{ $multiply: [{ $divide: ["$sobre_40", "$n"] }, 100] }, 1] } } },
    { $sort: { no2_prom: -1 } },
  ]));

ejecutar("P5. ¿Cuál es la evolución de una variable durante un período? (perfil horario, julio 2025)", () =>
  db.lecturas.aggregate([
    { $match: { tipo: { $in: ["clima", "trafico", "calidad_aire"] },
                ts: { $gte: ISODate("2025-06-30T22:00:00Z"), $lt: ISODate("2025-07-31T22:00:00Z") } } },
    { $group: { _id: { $hour: { date: "$ts", timezone: TZ } },
                temperatura: { $avg: "$mediciones.temperatura" },
                intensidad: { $avg: "$mediciones.intensidad" },
                no2: { $avg: "$mediciones.no2" }, o3: { $avg: "$mediciones.o3" } } },
    { $project: { _id: 0, hora: "$_id", temperatura: { $round: ["$temperatura", 1] },
                  intensidad: { $round: ["$intensidad", 0] }, no2: { $round: ["$no2", 1] }, o3: { $round: ["$o3", 1] } } },
    { $sort: { hora: 1 } },
  ]));

ejecutar("P6. Sensores que dejaron de enviar información (última lectura > 7 días antes del cierre)", () =>
  db.lecturas.aggregate([
    { $match: { tipo: { $ne: "estacionamiento" } } },  // los aparcamientos reportan mensualmente
    { $group: { _id: "$sensor_id", ultima_lectura: { $max: "$ts" }, tipo: { $first: "$tipo" },
                zona: { $first: "$ubicacion.zona" } } },
    { $match: { ultima_lectura: { $lt: ISODate("2025-12-24T23:00:00Z") } } },
    { $sort: { ultima_lectura: 1 } },
  ]));

ejecutar("P7. ¿Qué mide cada estación de aire? (la semiestructura que justifica el modelo documental)", () =>
  db.lecturas.aggregate([
    { $match: { tipo: "calidad_aire", ts: ANIO_2025 } },
    { $project: { sensor_id: 1, variables: { $map: { input: { $objectToArray: "$mediciones" }, in: "$$this.k" } } } },
    { $unwind: "$variables" },
    { $group: { _id: "$sensor_id", variables: { $addToSet: "$variables" } } },
    { $project: { _id: 0, sensor: "$_id", n_variables: { $size: "$variables" },
                  variables: { $sortArray: { input: "$variables", sortBy: 1 } } } },
    { $sort: { n_variables: -1, sensor: 1 } },
  ]));
