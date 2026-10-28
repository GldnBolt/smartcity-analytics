// =============================================================================
// 00_esquema.js — Crea (o recrea) las colecciones de SmartCity con validación $jsonSchema
//
//   docker compose exec mongo mongosh /scripts/00_esquema.js
//
// Decisión de diseño: "esquema flexible, pero no anárquico".
//   - Se valida lo que TODA lectura debe tener (sensor, tiempo, tipo, ubicación GeoJSON).
//   - `mediciones` solo se exige como objeto no vacío: cada tipo de sensor trae campos distintos,
//     e incluso dentro del mismo tipo cada estación real de Madrid mide un subconjunto distinto
//     (p. ej. solo algunas estaciones de aire miden PM2.5 u O3). Un sensor nuevo (p. ej.
//     flujo_peatonal) se incorpora sin migraciones ni ALTER TABLE.
//   - No se validan rangos físicos: MongoDB guarda el dato tal como lo publicó la red, y los
//     valores que la red marcó como NO validados se conservan aparte en meta.no_validados.
//     La limpieza ocurre después en Spark. Así no se pierde evidencia.
// =============================================================================
db = db.getSiblingDB("smartcity");

const geoPoint = {
  bsonType: "object",
  required: ["type", "coordinates"],
  properties: {
    type: { enum: ["Point"] },
    coordinates: { bsonType: "array", minItems: 2, maxItems: 2, items: { bsonType: "double" } },
  },
};

// zona = distrito municipal de Madrid (D01..D21); area = almendra central / periferia
const ubicacion = {
  bsonType: "object",
  required: ["zona_id", "zona", "geo"],
  properties: {
    zona_id: { bsonType: "string", pattern: "^D\\d{2}$" },
    zona: { bsonType: "string" },
    area: { enum: ["Almendra central", "Periferia"] },
    geo: geoPoint,
  },
};
const sensorId = { bsonType: "string", pattern: "^[A-Z]{3}-[A-Za-z0-9-]+$", description: "id_sensor del catálogo" };

const colecciones = {
  lecturas: {
    bsonType: "object",
    required: ["sensor_id", "tipo", "ts", "ubicacion", "mediciones"],
    properties: {
      sensor_id: sensorId,
      tipo: { bsonType: "string" },
      ts: { bsonType: "date", description: "UTC" },
      ubicacion: ubicacion,
      mediciones: { bsonType: "object", minProperties: 1 },
      meta: { bsonType: "object" },
    },
  },
  alertas: {
    bsonType: "object",
    required: ["sensor_id", "ts", "ubicacion", "variable", "valor", "umbral", "nivel", "estado"],
    properties: {
      sensor_id: sensorId,
      ts: { bsonType: "date" },
      ubicacion: ubicacion,
      variable: { bsonType: "string" },
      valor: { bsonType: ["double", "int", "long"] },
      umbral: { bsonType: ["double", "int", "long"] },
      nivel: { enum: ["advertencia", "critica"] },
      estado: { enum: ["abierta", "atendida"] },
    },
  },
  eventos: {
    bsonType: "object",
    required: ["ts", "tipo_evento", "severidad", "payload"],
    properties: {
      ts: { bsonType: "date" },
      tipo_evento: { bsonType: "string" },
      severidad: { enum: ["baja", "media", "alta", "critica"] },
      payload: { bsonType: "object" },
    },
  },
  incidentes: {
    bsonType: "object",
    required: ["categoria", "ts", "descripcion", "estado", "ubicacion"],
    properties: {
      categoria: { bsonType: "string" },
      ts: { bsonType: "date" },
      descripcion: { bsonType: "string" },
      estado: { enum: ["reportado", "en_atencion", "resuelto"] },
      prioridad: { enum: ["baja", "media", "alta"] },
      // Los incidentes de Calle 30 no traen coordenadas: se ubican por vía y tramo
      ubicacion: { bsonType: "object", required: ["via"], properties: { via: { bsonType: "string" } } },
      detalle: { bsonType: "object" },
      etiquetas: { bsonType: "array", items: { bsonType: "string" } },
      comentarios: { bsonType: "array" },
    },
  },
};

for (const [nombre, schema] of Object.entries(colecciones)) {
  db.getCollection(nombre).drop();
  db.createCollection(nombre, {
    validator: { $jsonSchema: schema },
    validationLevel: "strict",
    validationAction: "error",
  });
  print(`✔ colección ${nombre} creada con validación`);
}
