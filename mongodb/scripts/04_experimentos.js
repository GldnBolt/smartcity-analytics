// =============================================================================
// 04_experimentos.js — Evidencia experimental para el documento técnico
//
//   docker compose exec mongo mongosh /scripts/04_experimentos.js
//
// A. Índices vs. escaneo completo: la misma consulta con su índice y forzando COLLSCAN
//    (hint {$natural:1}). No hace falta borrar índices, así que no daña el dataset.
// B. Colección normal vs. time series collection (almacenamiento y tiempo de agregación).
// C. Velocidad de ingesta: insertOne uno a uno vs. insertMany por lotes.
// =============================================================================
db = db.getSiblingDB("smartcity");

function etapas(plan) {
  const s = [plan.stage];
  if (plan.inputStage) s.push(...etapas(plan.inputStage));
  if (plan.inputStages) plan.inputStages.forEach((p) => s.push(...etapas(p)));
  return s;
}

function medir(cursorFn) {
  const ex = cursorFn().explain("executionStats");
  const st = ex.executionStats;
  return {
    plan: etapas(ex.queryPlanner.winningPlan.queryPlan ?? ex.queryPlanner.winningPlan).join(" ← "),
    ms: st.executionTimeMillis,
    docs_examinados: st.totalDocsExamined,
    claves_examinadas: st.totalKeysExamined,
    devueltos: st.nReturned,
  };
}

// -----------------------------------------------------------------------------
print("\n================ A. Índice vs. COLLSCAN ================");
const total = db.lecturas.estimatedDocumentCount();
print(`Colección lecturas: ${total.toLocaleString()} documentos\n`);

const experimentos = [
  {
    nombre: "Q1 últimas 20 lecturas de un sensor",
    col: "lecturas",
    filtro: { sensor_id: "AIR-008" },
    sort: { ts: -1 }, limit: 20,
  },
  {
    nombre: "Q2 lecturas de aire en una semana",
    col: "lecturas",
    filtro: { tipo: "calidad_aire", ts: { $gte: ISODate("2025-11-30T23:00:00Z"), $lt: ISODate("2025-12-07T23:00:00Z") } },
  },
  {
    nombre: "Q6 lecturas de una zona en un mes",
    col: "lecturas",
    filtro: { "ubicacion.zona": "Carabanchel", ts: { $gte: ISODate("2025-10-31T23:00:00Z"), $lt: ISODate("2025-11-30T23:00:00Z") } },
  },
  {
    nombre: "Alertas abiertas recientes (índice parcial)",
    col: "alertas",
    filtro: { estado: "abierta" },
    sort: { ts: -1 }, limit: 50,
  },
];

const tabla = [];
for (const e of experimentos) {
  const base = () => {
    let c = db.getCollection(e.col).find(e.filtro);
    if (e.sort) c = c.sort(e.sort);
    if (e.limit) c = c.limit(e.limit);
    return c;
  };
  const conIndice = medir(base);
  const sinIndice = medir(() => base().hint({ $natural: 1 }));
  tabla.push({ consulta: e.nombre, modo: "con índice", ...conIndice });
  tabla.push({ consulta: e.nombre, modo: "COLLSCAN", ...sinIndice });
  print(`${e.nombre}`);
  print(`   con índice: ${String(conIndice.ms).padStart(5)} ms, ${conIndice.docs_examinados.toLocaleString().padStart(9)} docs examinados  [${conIndice.plan}]`);
  print(`   COLLSCAN:   ${String(sinIndice.ms).padStart(5)} ms, ${sinIndice.docs_examinados.toLocaleString().padStart(9)} docs examinados  [${sinIndice.plan}]`);
  print(`   → ${(sinIndice.docs_examinados / Math.max(1, conIndice.docs_examinados)).toFixed(0)}x menos documentos leídos con índice\n`);
}

// -----------------------------------------------------------------------------
print("\n================ B. Colección normal vs. time series ================");
// Las time series collections agrupan internamente las lecturas en "buckets" por sensor y
// ventana de tiempo, comprimiendo columnas. Se crea una copia con $out para comparar.
db.lecturas_ts.drop();
let t = Date.now();
db.lecturas.aggregate([
  // mismo contenido que la colección normal; lo constante por sensor pasa al metaField
  { $project: { _id: 0, ts: 1, mediciones: 1, meta: 1,
                sensor: { id: "$sensor_id", tipo: "$tipo", ubicacion: "$ubicacion" } } },
  { $out: { db: "smartcity", coll: "lecturas_ts",
            timeseries: { timeField: "ts", metaField: "sensor", granularity: "hours" } } },
], { allowDiskUse: true });
print(`Copia a time series: ${((Date.now() - t) / 1000).toFixed(1)} s`);

const sNormal = db.runCommand({ collStats: "lecturas" });
const sTs = db.runCommand({ collStats: "lecturas_ts" });
const mb = (b) => (b / 1048576).toFixed(1) + " MB";
print(`                      normal        time series`);
print(`  storageSize       ${mb(sNormal.storageSize).padStart(10)}   ${mb(sTs.storageSize).padStart(12)}`);
print(`  totalIndexSize    ${mb(sNormal.totalIndexSize).padStart(10)}   ${mb(sTs.totalIndexSize).padStart(12)}`);
print(`  buckets           ${"-".padStart(10)}   ${String(sTs.timeseries.bucketCount).padStart(12)}  (${(sTs.timeseries.numBytesUncompressed / 1048576).toFixed(0)} MB → ${(sTs.timeseries.numBytesCompressed / 1048576).toFixed(0)} MB por compresión columnar)`);

function tiempoAgg(col, campoTipo, campoZona) {
  const ini = Date.now();
  db.getCollection(col).aggregate([
    { $match: { [campoTipo]: "calidad_aire" } },
    { $group: { _id: { zona: `$${campoZona}`, mes: { $dateTrunc: { date: "$ts", unit: "month" } } },
                no2: { $avg: "$mediciones.no2" } } },
  ]).toArray();
  return Date.now() - ini;
}
print(`  NO2 mensual/zona  ${String(tiempoAgg("lecturas", "tipo", "ubicacion.zona")).padStart(7)} ms   ${String(tiempoAgg("lecturas_ts", "sensor.tipo", "sensor.ubicacion.zona")).padStart(9)} ms`);
print("  (Compromiso: la time series ahorra espacio y acelera agregaciones temporales, pero tiene");
print("   restricciones en updates/deletes y en índices; por eso `lecturas` se mantiene como colección");
print("   normal para poder demostrar todas las operaciones CRUD.)");
db.lecturas_ts.drop();

// -----------------------------------------------------------------------------
print("\n================ C. Velocidad de ingesta ================");
const N = 5000;
const muestra = db.lecturas.find({}, { _id: 0 }).limit(N).toArray();
db.ingesta_test.drop();

t = Date.now();
muestra.forEach((d) => db.ingesta_test.insertOne(d));
const msUno = Date.now() - t;
db.ingesta_test.drop();

t = Date.now();
db.ingesta_test.insertMany(muestra, { ordered: false });
const msLote = Date.now() - t;
db.ingesta_test.drop();

print(`  insertOne × ${N}:  ${msUno} ms  → ${Math.round(N / (msUno / 1000)).toLocaleString()} lecturas/s`);
print(`  insertMany(${N}):  ${msLote} ms  → ${Math.round(N / (msLote / 1000)).toLocaleString()} lecturas/s`);
print(`  → los gateways IoT deben enviar por lotes: ${(msUno / Math.max(1, msLote)).toFixed(0)}x más rápido`);
