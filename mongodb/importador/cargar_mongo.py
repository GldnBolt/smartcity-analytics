"""
Importa los datos reales de Madrid (data/raw/madrid) a MongoDB.

Los datos maestros se LEEN DE POSTGRESQL (fuente de verdad): qué sensores existen, su posición,
barrio y distrito, y los umbrales de alerta. Por eso PostgreSQL debe estar cargado antes.

  lecturas    aire (horario), clima (horario), tráfico (15 min -> horario), ruido (diario),
              estacionamientos (mensual) y energía (horario, SINTÉTICA a partir de la temperatura real)
  alertas     lecturas que superan los umbrales de PostgreSQL del mismo periodo que la lectura
  eventos     patrones detectados: episodios de contaminación, congestión, lluvia intensa, calor
              extremo, picos de consumo, datos no validados y huecos de transmisión (sensor sin datos)
  incidentes  accidentes e incidencias reales de Calle 30

Uso:  python -m mongodb.importador.cargar_mongo
"""
import os
import random
import time
from collections import defaultdict
from datetime import timedelta, timezone

import psycopg
from pymongo import MongoClient

from ingesta.comun import FECHA_FIN, RAW, TZ_MADRID, leer_csv, local_a_utc
from . import fuentes

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "smartcity")
PG_DSN = os.getenv("PG_DSN", "postgresql://smartcity:smartcity@localhost:5432/smartcity")
LOTE = 10_000
SEMILLA = int(os.getenv("SEMILLA", "2026"))

# Periodo de cada tipo de lectura: decide qué umbrales de PostgreSQL se le aplican
PERIODO = {"calidad_aire": "horario", "clima": "horario", "trafico": "horario", "energia": "horario",
           "ruido": "diario", "estacionamiento": "mensual"}

SQL_SENSORES = """
    SELECT s.codigo, s.tipo_sensor, s.latitud, s.longitud, d.codigo, d.nombre, d.area, u.nombre_zona
    FROM sensores s
    JOIN ubicaciones u ON u.id_ubicacion = s.id_ubicacion
    JOIN distritos d ON d.id = u.id_distrito"""
SQL_UMBRALES = """
    SELECT v.tipo_sensor, v.codigo, t.periodo, t.advertencia, t.critico, v.unidad
    FROM umbrales t JOIN variables v ON v.id = t.id_variable"""


def leer_maestros():
    with psycopg.connect(PG_DSN) as con:
        sensores = con.execute(SQL_SENSORES).fetchall()
        umbrales = con.execute(SQL_UMBRALES).fetchall()
    if not sensores:
        raise SystemExit("PostgreSQL no tiene sensores. Carga primero PostgreSQL (postgres/cargador).")
    return sensores, umbrales


class Buffer:
    def __init__(self, coleccion):
        self.col, self.docs, self.total = coleccion, [], 0

    def agregar(self, docs):
        self.docs.extend(docs)
        if len(self.docs) >= LOTE:
            self.vaciar()

    def vaciar(self):
        if self.docs:
            self.col.insert_many(self.docs, ordered=False)
            self.total += len(self.docs)
            self.docs = []


class Cargador:
    def __init__(self, db):
        self.db = db
        self.rng = random.Random(SEMILLA)
        sensores, umbrales = leer_maestros()
        self.tipo = {cod: tipo for cod, tipo, *_ in sensores}
        self.distrito = {cod: int(dcod[1:]) for cod, _, _, _, dcod, *_ in sensores}
        # El distrito (zona) y el barrio se copian en cada lectura: las agregaciones no necesitan join
        self.ubicacion = {
            cod: {"zona_id": dcod, "zona": dnom, "area": area, "barrio": barrio,
                  "geo": {"type": "Point", "coordinates": [float(lon), float(lat)]}}
            for cod, _, lat, lon, dcod, dnom, area, barrio in sensores}
        self.umbrales = defaultdict(list)
        for tipo, variable, periodo, adv, crit, unidad in umbrales:
            if periodo == PERIODO.get(tipo):
                adv = float(adv) if adv is not None else float(crit)
                crit = float(crit) if crit is not None else float("inf")
                self.umbrales[tipo].append((variable, adv, crit, unidad))
        self.buf = {c: Buffer(db[c]) for c in ("lecturas", "alertas", "eventos", "incidentes")}
        self.fin_utc = local_a_utc(FECHA_FIN.year, FECHA_FIN.month, FECHA_FIN.day, 23, 59)
        self.no_validados = defaultdict(lambda: [set(), 0])  # (sensor, día local) -> (variables, horas)
        self.temperaturas = defaultdict(lambda: [0.0, 0])     # ts -> (suma, n) para el modelo de energía
        self.desconocidos = defaultdict(int)

    # ------------------------------------------------------------------ lecturas
    def cargar(self, nombre, docs):
        t0, n = time.time(), 0
        for doc in docs:
            sid = doc["sensor_id"]
            if sid not in self.ubicacion:
                self.desconocidos[sid] += 1  # estación sin ficha en el catálogo (p. ej. dada de baja)
                continue
            doc["ubicacion"] = self.ubicacion[sid]
            self.buf["lecturas"].agregar([doc])
            self.buf["alertas"].agregar(self.alertas(doc))
            self.buf["eventos"].agregar(self.eventos(doc))
            n += 1
        self.buf["lecturas"].vaciar()
        print(f"  {nombre:<16} {n:>10,} lecturas  ({time.time() - t0:.0f}s)")

    def alertas(self, doc):
        res = []
        for variable, adv, crit, unidad in self.umbrales.get(doc["tipo"], ()):
            valor = doc["mediciones"].get(variable)
            if valor is None or valor < adv:
                continue
            nivel = "critica" if valor >= crit else "advertencia"
            a = {"sensor_id": doc["sensor_id"], "tipo_sensor": doc["tipo"], "ts": doc["ts"],
                 "ubicacion": doc["ubicacion"], "variable": variable, "valor": valor,
                 "umbral": crit if nivel == "critica" else adv, "unidad": unidad,
                 "nivel": nivel, "estado": "abierta"}
            # El flujo de atención (operador) no existe en los datos abiertos: se SIMULA para poder
            # demostrar el ciclo de vida de una alerta. Las de los últimos 10 días quedan abiertas.
            if self.fin_utc - doc["ts"] > timedelta(days=10) and self.rng.random() < 0.93:
                a["estado"] = "atendida"
                a["atencion"] = {"fecha": doc["ts"] + timedelta(minutes=self.rng.randint(5, 600)),
                                 "operador": f"OP-{self.rng.randint(1, 25):02d}", "simulada": True}
            res.append(a)
        return res

    def eventos(self, doc):
        m, tipo, ev = doc["mediciones"], doc["tipo"], []
        base = {"sensor_id": doc["sensor_id"], "ts": doc["ts"], "ubicacion": doc["ubicacion"]}

        def evento(tipo_evento, severidad, **payload):
            ev.append({**base, "tipo_evento": tipo_evento, "severidad": severidad, "payload": payload})

        if tipo == "calidad_aire":
            if m.get("no2", 0) >= 200 or m.get("o3", 0) >= 180 or m.get("pm10", 0) >= 100:
                crit = m.get("no2", 0) >= 400 or m.get("o3", 0) >= 240
                evento("episodio_contaminacion", "critica" if crit else "alta",
                       **{k: m[k] for k in ("no2", "o3", "pm10", "pm25") if k in m})
        elif tipo == "clima":
            if m.get("precipitacion", 0) >= 10:
                evento("lluvia_intensa", "alta", precipitacion=m["precipitacion"], humedad=m.get("humedad"))
            if m.get("temperatura", 0) >= 38:
                evento("calor_extremo", "alta", temperatura=m["temperatura"], humedad=m.get("humedad"))
            if "temperatura" in m:
                t = self.temperaturas[doc["ts"]]
                t[0] += m["temperatura"]; t[1] += 1
        elif tipo == "trafico":
            if m.get("carga", 0) >= 90:
                evento("congestion", "alta", carga=m["carga"], intensidad=m.get("intensidad"),
                       velocidad_prom=m.get("velocidad_prom"))
        elif tipo == "energia":
            if m["consumo_kwh"] >= 1900:
                evento("pico_consumo", "media", consumo_kwh=m["consumo_kwh"], temperatura=m["temperatura_ref"])
        if "no_validados" in doc["meta"]:
            dia_local = doc["ts"].replace(tzinfo=timezone.utc).astimezone(TZ_MADRID).date()
            clave = (doc["sensor_id"], dia_local)
            nv = self.no_validados[clave]
            nv[0].update(doc["meta"]["no_validados"]); nv[1] += 1
        return ev

    def eventos_no_validados(self):
        """Un evento por sensor y día con datos que la red marcó como NO validados ('N')."""
        for (sid, dia), (variables, horas) in self.no_validados.items():
            self.buf["eventos"].agregar([{
                "sensor_id": sid, "ts": local_a_utc(dia.year, dia.month, dia.day),
                "ubicacion": self.ubicacion[sid], "tipo_evento": "dato_no_validado",
                "severidad": "baja", "payload": {"variables": sorted(variables), "horas": horas}}])

    def eventos_huecos(self, horas_min=12):
        """Huecos de transmisión: calculados en MongoDB con $setWindowFields ($shift) y volcados con $merge."""
        self.buf["eventos"].vaciar()
        self.db.lecturas.aggregate([
            {"$match": {"tipo": {"$in": ["calidad_aire", "clima", "trafico"]}}},
            {"$setWindowFields": {"partitionBy": "$sensor_id", "sortBy": {"ts": 1},
                                  "output": {"anterior": {"$shift": {"output": "$ts", "by": -1}}}}},
            {"$set": {"horas": {"$dateDiff": {"startDate": "$anterior", "endDate": "$ts", "unit": "hour"}}}},
            {"$match": {"horas": {"$gt": horas_min}}},
            {"$project": {"_id": 0, "sensor_id": 1, "ts": "$anterior", "ubicacion": 1,
                          "tipo_evento": "sensor_sin_datos",
                          "severidad": {"$cond": [{"$gte": ["$horas", 72]}, "alta", "media"]},
                          "payload": {"desde": "$anterior", "hasta": "$ts", "horas": "$horas"}}},
            {"$merge": {"into": "eventos", "whenNotMatched": "insert"}},
        ], allowDiskUse=True)

def main():
    db = MongoClient(MONGO_URI)[MONGO_DB]
    if db.lecturas.estimated_document_count() > 0:
        raise SystemExit("La colección 'lecturas' ya tiene datos. Ejecuta primero scripts/00_esquema.js.")
    c = Cargador(db)
    t0 = time.time()
    # Atributos que solo describen el dato de origen: se leen de los archivos de la fuente
    tipo_estacion = {f"AIR-{int(e['CODIGO_CORTO']):03d}": e["NOM_TIPO"]
                     for e in leer_csv(RAW / "aire/estaciones_aire.csv")}
    tipo_elem = {p["id"]: p["tipo_elem"] for p in leer_csv(RAW / "trafico/puntos_trafico_2025.csv")
                 if f"TRF-{p['id']}" in c.tipo}
    distritos = {cod: c.distrito[cod] for cod, tipo in c.tipo.items() if tipo == "energia"}

    print("Importando lecturas reales de Madrid:")
    c.cargar("calidad_aire", fuentes.lecturas_aire(tipo_estacion))
    c.cargar("clima", fuentes.lecturas_clima())
    c.cargar("ruido", fuentes.lecturas_ruido())
    c.cargar("estacionamiento", fuentes.lecturas_estacionamiento())
    temp = {ts: s / n for ts, (s, n) in c.temperaturas.items()}
    c.cargar("energia (sint.)", fuentes.lecturas_energia(distritos, temp, SEMILLA))
    c.cargar("trafico", fuentes.lecturas_trafico(tipo_elem))

    print("Incidentes y eventos derivados:")
    for doc in fuentes.incidentes_calle30():
        c.buf["incidentes"].agregar([doc])
    c.eventos_no_validados()
    for b in c.buf.values():
        b.vaciar()
    c.eventos_huecos()
    if c.desconocidos:
        print(f"  lecturas omitidas de estaciones que no están en PostgreSQL: {dict(c.desconocidos)}")

    print(f"\nCarga terminada en {time.time() - t0:.0f}s:")
    for nombre in ("lecturas", "alertas", "eventos", "incidentes"):
        print(f"  {nombre:<11} {db[nombre].estimated_document_count():>10,}")


if __name__ == "__main__":
    main()
