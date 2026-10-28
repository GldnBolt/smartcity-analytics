// =============================================================================
// 01_indices.js — Índices de SmartCity
//
//   docker compose exec mongo mongosh /scripts/01_indices.js
//
// Se crean DESPUÉS de la carga masiva: insertar 1M documentos sin índices secundarios y luego
// construirlos es mucho más rápido que mantener 5+ índices durante cada insertMany.
//
// Regla ESR (Equality, Sort, Range): en los compuestos va primero el campo filtrado por igualdad
// (sensor_id, zona, tipo) y luego `ts`, que se usa para ordenar y para rangos de fechas.
// =============================================================================
db = db.getSiblingDB("smartcity");

function crear(col, spec, opts = {}) {
  const t = Date.now();
  const nombre = db.getCollection(col).createIndex(spec, opts);
  print(`✔ ${col}.${nombre}  (${Date.now() - t} ms)`);
}

// --- lecturas -----------------------------------------------------------------
crear("lecturas", { sensor_id: 1, ts: -1 });          // Q1: lecturas de un sensor, más recientes primero
crear("lecturas", { tipo: 1, ts: -1 });               // Q2/Q7: por tipo + rango de fechas
crear("lecturas", { "ubicacion.zona": 1, ts: -1 });   // Q6/Q10: por zona + rango de fechas
crear("lecturas", { ts: 1 });                         // rangos de fechas globales, exportación incremental a HDFS
crear("lecturas", { "ubicacion.geo": "2dsphere" });   // consultas espaciales ($near, $geoWithin)

// --- alertas ------------------------------------------------------------------
crear("alertas", { "ubicacion.zona": 1, ts: -1 });    // Q5: alertas por zona
crear("alertas", { sensor_id: 1, ts: -1 });
crear("alertas", { nivel: 1, ts: -1 });
crear("alertas", { "ubicacion.geo": "2dsphere" });    // Q5b: $geoNear
// Índice parcial: solo indexa las alertas abiertas (pocas), que es lo que consulta el operador.
crear("alertas", { ts: -1 }, { name: "abiertas_ts", partialFilterExpression: { estado: "abierta" } });

// --- eventos ------------------------------------------------------------------
crear("eventos", { tipo_evento: 1, ts: -1 });
crear("eventos", { severidad: 1, ts: -1 });           // Q4: eventos críticos
crear("eventos", { sensor_id: 1, ts: -1 });

// --- incidentes ---------------------------------------------------------------
crear("incidentes", { categoria: 1, ts: -1 });        // Q8
crear("incidentes", { etiquetas: 1 });                // índice multikey sobre un arreglo
crear("incidentes", { "ubicacion.via": 1, ts: -1 });  // Calle 30 no publica coordenadas: se consulta por vía
// Índice de texto: búsqueda libre en la descripción (texto real de los operadores) y en comentarios
crear("incidentes", { descripcion: "text", "comentarios.texto": "text" },
      { name: "texto_incidentes", default_language: "spanish", weights: { descripcion: 3, "comentarios.texto": 1 } });

print("\nÍndices por colección:");
for (const c of ["lecturas", "alertas", "eventos", "incidentes"]) {
  const s = db.getCollection(c).stats();
  print(`  ${c.padEnd(11)} ${String(s.nindexes).padStart(2)} índices, ${(s.totalIndexSize / 1048576).toFixed(1)} MB`);
}
