// =============================================================================
// 02_crud.js — Operaciones CRUD, filtros, operadores, proyecciones y ordenamiento
//
//   docker compose exec mongo mongosh /scripts/02_crud.js
//
// Todos los documentos de demostración llevan meta.demo = true y se limpian al final, y los
// cambios sobre documentos reales se revierten: el script se puede ejecutar varias veces.
// =============================================================================
db = db.getSiblingDB("smartcity");
const titulo = (t) => print(`\n========== ${t} ==========`);
const sol = { zona_id: "D01", zona: "Centro", area: "Almendra central",
              geo: { type: "Point", coordinates: [-3.7038, 40.4168] } };  // Puerta del Sol

// -----------------------------------------------------------------------------
titulo("insertOne — un tipo de sensor NUEVO sin modificar el esquema");
// En PostgreSQL esto requeriría ALTER TABLE o una tabla nueva. Aquí el documento simplemente
// trae sus propios campos dentro de `mediciones`.
const r1 = db.lecturas.insertOne({
  sensor_id: "PEA-001",
  tipo: "flujo_peatonal",
  ts: new Date(),
  ubicacion: sol,
  mediciones: { personas_entrando: 1320, personas_saliendo: 980, direccion_predominante: "norte" },
  meta: { demo: true, camara: { resolucion: "1080p", anonimizado: true } },
});
printjson(r1);

// -----------------------------------------------------------------------------
titulo("insertMany — lote de lecturas heterogéneas (como llega desde un gateway IoT)");
const r2 = db.lecturas.insertMany([
  { sensor_id: "AIR-035", tipo: "calidad_aire", ts: new Date(), ubicacion: sol,
    mediciones: { no2: 41, pm10: 22, o3: 35 }, meta: { demo: true } },
  { sensor_id: "MET-035", tipo: "clima", ts: new Date(), ubicacion: sol,
    mediciones: { temperatura: 27.2, humedad: 34 }, meta: { demo: true } },
  { sensor_id: "TRF-3409", tipo: "trafico", ts: new Date(), ubicacion: sol,
    mediciones: { intensidad: 640, ocupacion_pct: 18, carga: 81, indice_congestion: 0.81 }, meta: { demo: true } },
]);
printjson(r2);

// -----------------------------------------------------------------------------
titulo("Validación — un documento sin `ts` es rechazado por el $jsonSchema");
try {
  db.lecturas.insertOne({ sensor_id: "MET-035", tipo: "clima", ubicacion: sol,
                          mediciones: { temperatura: 20 }, meta: { demo: true } });
} catch (e) {
  print(`Rechazado, falta: ${e.errInfo?.details?.schemaRulesNotSatisfied?.[0]?.missingProperties ?? e.message}`);
}

// -----------------------------------------------------------------------------
titulo("findOne — última lectura de Escuelas Aguirre (proyección + ordenamiento)");
printjson(db.lecturas.findOne(
  { sensor_id: "AIR-008", "meta.demo": { $exists: false } },
  { _id: 0, ts: 1, mediciones: 1, "ubicacion.zona": 1 },
  { sort: { ts: -1 } }
));

// -----------------------------------------------------------------------------
titulo("find — operadores de comparación y lógicos ($gte, $lt, $in, $or)");
// Horas con NO2 o PM10 altos en distritos del sur durante diciembre de 2025
printjson(db.lecturas.find(
  {
    tipo: "calidad_aire",
    ts: { $gte: ISODate("2025-11-30T23:00:00Z"), $lt: ISODate("2025-12-31T23:00:00Z") },
    "ubicacion.zona": { $in: ["Carabanchel", "Villaverde", "Villa de Vallecas", "Puente de Vallecas"] },
    $or: [{ "mediciones.no2": { $gt: 100 } }, { "mediciones.pm10": { $gt: 50 } }],
  },
  { _id: 0, sensor_id: 1, ts: 1, "ubicacion.zona": 1, "mediciones.no2": 1, "mediciones.pm10": 1 }
).sort({ "mediciones.no2": -1 }).limit(5).toArray());

// -----------------------------------------------------------------------------
titulo("find — $exists: campos que solo tienen algunos documentos (esquema heterogéneo real)");
print(`Lecturas de aire con PM2.5:                 ${db.lecturas.countDocuments({ tipo: "calidad_aire", "mediciones.pm25": { $exists: true } })}`);
print(`Lecturas de aire SIN PM2.5:                 ${db.lecturas.countDocuments({ tipo: "calidad_aire", "mediciones.pm25": { $exists: false } })}`);
print(`Lecturas de tráfico con velocidad (C-30):   ${db.lecturas.countDocuments({ tipo: "trafico", "mediciones.velocidad_prom": { $exists: true } })}`);
print(`Lecturas con datos no validados por la red: ${db.lecturas.countDocuments({ "meta.no_validados": { $exists: true } })}`);
print(`Lecturas sintéticas (energía):              ${db.lecturas.countDocuments({ "meta.fuente": "sintetico" })}`);

// -----------------------------------------------------------------------------
titulo("find — consultas sobre arreglos y subdocumentos ($all, $size, campos anidados)");
// Accidentes en túnel con heridos graves (etiquetas es un arreglo; detalle es un subdocumento)
printjson(db.incidentes.find(
  { etiquetas: { $all: ["tunel", "con_heridos"] }, "detalle.heridos_graves": { $gte: 1 } },
  { _id: 0, ts: 1, categoria: 1, "ubicacion.via": 1, detalle: 1, etiquetas: 1 }
).sort({ ts: -1 }).limit(3).toArray());
print(`Incidentes con exactamente 3 etiquetas: ${db.incidentes.countDocuments({ etiquetas: { $size: 3 } })}`);

// -----------------------------------------------------------------------------
titulo("find — búsqueda de texto completo sobre las descripciones reales ($text)");
printjson(db.incidentes.find(
  { $text: { $search: "patinete atropello" } },
  { _id: 0, ts: 1, categoria: 1, descripcion: 1, score: { $meta: "textScore" } }
).sort({ score: { $meta: "textScore" } }).limit(3).toArray());

// -----------------------------------------------------------------------------
titulo("find — consulta geoespacial ($near): estaciones de aire a menos de 1,5 km de Sol");
printjson(db.lecturas.aggregate([
  { $geoNear: { near: sol.geo, distanceField: "distancia_m", maxDistance: 1500, spherical: true,
                query: { tipo: "calidad_aire", "meta.demo": { $exists: false } } } },
  { $group: { _id: "$sensor_id", distancia_m: { $first: { $round: ["$distancia_m", 0] } } } },
  { $sort: { distancia_m: 1 } },
]).toArray());

// -----------------------------------------------------------------------------
titulo("updateOne — un operador atiende una alerta crítica abierta");
const alerta = db.alertas.findOne({ estado: "abierta", nivel: "critica" }, { sort: { ts: -1 } });
if (alerta) {
  printjson(db.alertas.updateOne(
    { _id: alerta._id },
    {
      $set: { estado: "atendida", atencion: { fecha: new Date(), operador: "OP-DEMO" } },
      $push: { bitacora: { accion: "atendida_demo", fecha: new Date() } },  // campo nuevo solo en este doc
    }
  ));
  db.alertas.updateOne({ _id: alerta._id }, { $set: { estado: "abierta" }, $unset: { atencion: "", bitacora: "" } });
}

// -----------------------------------------------------------------------------
titulo("updateOne — comentario sobre un incidente ($push a un arreglo embebido) y $elemMatch");
const inc = db.incidentes.findOne({ categoria: "atropello" });
printjson(db.incidentes.updateOne(
  { _id: inc._id },
  { $push: { comentarios: { autor: "OP-DEMO", texto: "Se solicita revisar la señalización del tramo", fecha: new Date(), demo: true } } }
));
print("Incidentes con un comentario que mencione 'señalización':");
printjson(db.incidentes.find(
  { comentarios: { $elemMatch: { texto: /señalización/i, demo: true } } },
  { _id: 0, numero: 1, categoria: 1, "comentarios.texto": 1 }
).toArray());
db.incidentes.updateOne({ _id: inc._id }, { $pull: { comentarios: { demo: true } } });
db.incidentes.updateMany({ comentarios: { $size: 0 } }, { $unset: { comentarios: "" } });

// -----------------------------------------------------------------------------
titulo("updateMany — marcar como 'sospechosas' las lecturas de demo con congestión > 0.8");
printjson(db.lecturas.updateMany(
  { "meta.demo": true, "mediciones.indice_congestion": { $gt: 0.8 } },
  { $set: { "meta.revision": "sospechosa" }, $inc: { "meta.revisiones": 1 } }
));

titulo("updateMany — atender en bloque todas las advertencias abiertas de un distrito");
const filtroBloque = { estado: "abierta", nivel: "advertencia", "ubicacion.zona": "Retiro" };
const ids = db.alertas.find(filtroBloque, { _id: 1 }).toArray().map((d) => d._id);
printjson(db.alertas.updateMany(
  { _id: { $in: ids } },
  { $set: { estado: "atendida", atencion: { fecha: new Date(), operador: "OP-DEMO-LOTE" } } }
));
db.alertas.updateMany({ _id: { $in: ids } }, { $set: { estado: "abierta" }, $unset: { atencion: "" } });

// -----------------------------------------------------------------------------
titulo("deleteOne — eliminar la lectura del sensor nuevo");
printjson(db.lecturas.deleteOne({ _id: r1.insertedId }));

titulo("limpieza — deleteMany de los documentos de demostración");
printjson(db.lecturas.deleteMany({ "meta.demo": true }));
