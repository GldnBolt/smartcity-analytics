"""
Consultas MongoDB de SmartCity (datos reales de Madrid) para el dashboard (Streamlit).

Son los mismos pipelines de scripts/03_consultas_obligatorias.js, pero parametrizados con los
filtros dinámicos que exige la interfaz: rango de fechas, zona (distrito), tipo de sensor y sensor.
Cada función devuelve una lista de dicts (pd.DataFrame(resultado) la convierte directo).

    from mongodb.consultas import ConsultasMongo
    q = ConsultasMongo("mongodb://localhost:27017")
    q.q6_promedio_por_zona(desde=datetime(2025, 1, 1), tipo="calidad_aire")

Fechas: los filtros se reciben en hora local de Madrid; `ts` sale en UTC (usar a_local() al mostrar).
Prueba rápida:  python -m mongodb.consultas
"""
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pymongo import MongoClient

TZ = "Europe/Madrid"
TZ_MADRID = ZoneInfo(TZ)
FUENTE = "MongoDB"  # etiqueta de origen para cada gráfico del dashboard


def _local_a_utc(dt):
    """Fecha del filtro (hora local de Madrid, naive) -> UTC naive, como se guarda `ts`."""
    if dt is None:
        return None
    return dt.replace(tzinfo=TZ_MADRID).astimezone(timezone.utc).replace(tzinfo=None)


def a_local(ts):
    """`ts` UTC de MongoDB -> hora local de Madrid (para mostrar en el dashboard)."""
    return ts.replace(tzinfo=timezone.utc).astimezone(TZ_MADRID).replace(tzinfo=None)


class ConsultasMongo:
    def __init__(self, uri=None, db="smartcity"):
        self.db = MongoClient(uri or os.getenv("MONGO_URI", "mongodb://localhost:27017"))[db]

    # ------------------------------------------------------------------ filtros
    @staticmethod
    def _filtro(desde=None, hasta=None, zona=None, tipo=None, sensor=None, campo_tipo="tipo"):
        f = {}
        if desde or hasta:
            f["ts"] = {}
            if desde:
                f["ts"]["$gte"] = _local_a_utc(desde)
            if hasta:
                f["ts"]["$lt"] = _local_a_utc(hasta)
        if zona:
            f["ubicacion.zona"] = {"$in": list(zona)} if isinstance(zona, (list, tuple)) else zona
        if tipo:
            f[campo_tipo] = {"$in": list(tipo)} if isinstance(tipo, (list, tuple)) else tipo
        if sensor:
            f["sensor_id"] = sensor
        return f

    # ------------------------------------------------------------ catálogos UI
    def zonas(self):
        return sorted(self.db.lecturas.distinct("ubicacion.zona"))

    def tipos_sensor(self):
        return sorted(self.db.lecturas.distinct("tipo"))

    def sensores(self, tipo=None):
        return sorted(self.db.lecturas.distinct("sensor_id", {"tipo": tipo} if tipo else {}))

    def variables(self, tipo=None):
        """Variables disponibles (distintas por tipo y por estación: el esquema es flexible)."""
        return [d["_id"] for d in self.db.lecturas.aggregate([
            {"$match": {"tipo": tipo} if tipo else {}},
            {"$sample": {"size": 20000}},
            {"$project": {"k": {"$objectToArray": "$mediciones"}}}, {"$unwind": "$k"},
            {"$group": {"_id": "$k.k"}}, {"$sort": {"_id": 1}}])]

    # ------------------------------------------------- 10 consultas obligatorias
    def q1_lecturas_sensor(self, sensor, limite=100, **f):
        return list(self.db.lecturas.find(self._filtro(sensor=sensor, **f),
                                          {"_id": 0, "ts": 1, "tipo": 1, "mediciones": 1, "ubicacion.zona": 1})
                    .sort("ts", -1).limit(limite))

    def q2_lecturas_rango(self, desde, hasta, limite=500, **f):
        return list(self.db.lecturas.find(self._filtro(desde=desde, hasta=hasta, **f),
                                          {"_id": 0, "sensor_id": 1, "ts": 1, "tipo": 1,
                                           "ubicacion.zona": 1, "mediciones": 1})
                    .sort("ts", 1).limit(limite))

    def q3_max_min(self, **f):
        return list(self.db.lecturas.aggregate([
            {"$match": self._filtro(**f)},
            {"$project": {"tipo": 1, "ts": 1, "sensor_id": 1, "m": {"$objectToArray": "$mediciones"}}},
            {"$unwind": "$m"},
            {"$match": {"m.v": {"$type": "number"}}},
            {"$group": {"_id": {"tipo": "$tipo", "variable": "$m.k"},
                        "maximo": {"$top": {"sortBy": {"m.v": -1}, "output": {"valor": "$m.v", "sensor": "$sensor_id", "ts": "$ts"}}},
                        "minimo": {"$min": "$m.v"}}},
            {"$project": {"_id": 0, "tipo": "$_id.tipo", "variable": "$_id.variable", "maximo": "$maximo.valor",
                          "sensor_max": "$maximo.sensor", "ts_max": "$maximo.ts", "minimo": 1}},
            {"$sort": {"tipo": 1, "variable": 1}},
        ], allowDiskUse=True))

    def q4_eventos_criticos(self, limite=200, **f):
        filtro = self._filtro(**f)
        filtro["severidad"] = {"$in": ["alta", "critica"]}
        return list(self.db.eventos.find(filtro, {"_id": 0}).sort("ts", -1).limit(limite))

    def q4_eventos_por_tipo(self, **f):
        filtro = self._filtro(**f)
        filtro["severidad"] = {"$in": ["alta", "critica"]}
        return list(self.db.eventos.aggregate([
            {"$match": filtro},
            {"$group": {"_id": "$tipo_evento", "total": {"$sum": 1}}},
            {"$project": {"_id": 0, "tipo_evento": "$_id", "total": 1}},
            {"$sort": {"total": -1}},
        ]))

    def q5_alertas_por_zona(self, **f):
        return list(self.db.alertas.aggregate([
            {"$match": self._filtro(campo_tipo="tipo_sensor", **f)},
            {"$group": {"_id": "$ubicacion.zona",
                        "total": {"$sum": 1},
                        "criticas": {"$sum": {"$cond": [{"$eq": ["$nivel", "critica"]}, 1, 0]}},
                        "advertencias": {"$sum": {"$cond": [{"$eq": ["$nivel", "advertencia"]}, 1, 0]}},
                        "abiertas": {"$sum": {"$cond": [{"$eq": ["$estado", "abierta"]}, 1, 0]}},
                        "lon": {"$avg": {"$arrayElemAt": ["$ubicacion.geo.coordinates", 0]}},
                        "lat": {"$avg": {"$arrayElemAt": ["$ubicacion.geo.coordinates", 1]}}}},
            {"$project": {"_id": 0, "zona": "$_id", "total": 1, "criticas": 1, "advertencias": 1,
                          "abiertas": 1, "lat": 1, "lon": 1}},
            {"$sort": {"total": -1}},
        ]))

    def q5_alertas_cercanas(self, lon=-3.7038, lat=40.4168, radio_m=3000, nivel=None):
        query = {"nivel": nivel} if nivel else {}
        return list(self.db.alertas.aggregate([
            {"$geoNear": {"near": {"type": "Point", "coordinates": [lon, lat]}, "distanceField": "distancia_m",
                          "maxDistance": radio_m, "query": query, "spherical": True}},
            {"$limit": 500},
            {"$project": {"_id": 0, "ts": 1, "sensor_id": 1, "variable": 1, "valor": 1, "nivel": 1,
                          "zona": "$ubicacion.zona", "distancia_m": {"$round": ["$distancia_m", 0]},
                          "lon": {"$arrayElemAt": ["$ubicacion.geo.coordinates", 0]},
                          "lat": {"$arrayElemAt": ["$ubicacion.geo.coordinates", 1]}}},
        ]))

    VARIABLES_ZONA = ("no2", "pm10", "pm25", "o3", "temperatura", "humedad", "laeq_dia", "laeq_noche",
                      "intensidad", "carga", "consumo_kwh")

    def q6_promedio_por_zona(self, **f):
        return list(self.db.lecturas.aggregate([
            {"$match": self._filtro(**f)},
            {"$group": {"_id": "$ubicacion.zona", "lecturas": {"$sum": 1},
                        **{v: {"$avg": f"$mediciones.{v}"} for v in self.VARIABLES_ZONA}}},
            {"$project": {"_id": 0, "zona": "$_id", "lecturas": 1,
                          **{v: {"$round": [f"${v}", 2]} for v in self.VARIABLES_ZONA}}},
            {"$sort": {"zona": 1}},
        ]))

    def q7_promedio_por_tipo(self, **f):
        return list(self.db.lecturas.aggregate([
            {"$match": self._filtro(**f)},
            {"$project": {"tipo": 1, "m": {"$objectToArray": "$mediciones"}}},
            {"$unwind": "$m"},
            {"$match": {"m.v": {"$type": "number"}}},
            {"$group": {"_id": {"tipo": "$tipo", "variable": "$m.k"}, "promedio": {"$avg": "$m.v"}}},
            {"$project": {"_id": 0, "tipo": "$_id.tipo", "variable": "$_id.variable",
                          "promedio": {"$round": ["$promedio", 2]}}},
            {"$sort": {"tipo": 1, "variable": 1}},
        ], allowDiskUse=True))

    def q8_incidentes_por_categoria(self, desde=None, hasta=None, via=None):
        filtro = self._filtro(desde=desde, hasta=hasta)
        if via:
            filtro["ubicacion.via"] = via
        return list(self.db.incidentes.aggregate([
            {"$match": filtro},
            {"$group": {"_id": "$categoria", "total": {"$sum": 1},
                        "heridos_leves": {"$sum": "$detalle.heridos_leves"},
                        "heridos_graves": {"$sum": "$detalle.heridos_graves"},
                        "victimas_mortales": {"$sum": "$detalle.victimas_mortales"},
                        "en_tunel": {"$sum": {"$cond": [{"$in": ["tunel", {"$ifNull": ["$etiquetas", []]}]}, 1, 0]}}}},
            {"$project": {"_id": 0, "categoria": "$_id", "total": 1, "heridos_leves": 1, "heridos_graves": 1,
                          "victimas_mortales": 1, "en_tunel": 1}},
            {"$sort": {"total": -1}},
        ]))

    def q9_evolucion_mensual(self, variable="no2", **f):
        """Serie mensual por distrito con media móvil de 3 meses y variación interanual ($setWindowFields)."""
        filtro = self._filtro(**f)
        filtro[f"mediciones.{variable}"] = {"$exists": True}
        return list(self.db.lecturas.aggregate([
            {"$match": filtro},
            {"$group": {"_id": {"zona": "$ubicacion.zona",
                                "mes": {"$dateTrunc": {"date": "$ts", "unit": "month", "timezone": TZ}}},
                        "valor": {"$avg": f"$mediciones.{variable}"}}},
            {"$setWindowFields": {"partitionBy": "$_id.zona", "sortBy": {"_id.mes": 1},
                                  "output": {"media_movil_3m": {"$avg": "$valor", "window": {"documents": [-2, 0]}},
                                             "anio_anterior": {"$shift": {"output": "$valor", "by": -12}}}}},
            {"$project": {"_id": 0, "zona": "$_id.zona", "mes": "$_id.mes",
                          "valor": {"$round": ["$valor", 2]}, "media_movil_3m": {"$round": ["$media_movil_3m", 2]},
                          "anio_anterior": {"$round": ["$anio_anterior", 2]}}},
            {"$sort": {"zona": 1, "mes": 1}},
        ]))

    def q10_saturacion_hora_pico(self, carga_min=70, ocupacion_min=20, horas=(7, 8, 9, 17, 18, 19, 20), **f):
        filtro = self._filtro(tipo="trafico", **f)
        filtro.update({
            "mediciones.carga": {"$gte": carga_min},
            "mediciones.ocupacion_pct": {"$gte": ocupacion_min},
            "$expr": {"$and": [
                {"$in": [{"$hour": {"date": "$ts", "timezone": TZ}}, list(horas)]},
                {"$not": {"$in": [{"$dayOfWeek": {"date": "$ts", "timezone": TZ}}, [1, 7]]}},
            ]},
        })
        return list(self.db.lecturas.aggregate([
            {"$match": filtro},
            {"$group": {"_id": {"zona": "$ubicacion.zona", "hora": {"$hour": {"date": "$ts", "timezone": TZ}}},
                        "horas_saturadas": {"$sum": 1}, "carga_prom": {"$avg": "$mediciones.carga"},
                        "intensidad_prom": {"$avg": "$mediciones.intensidad"}}},
            {"$project": {"_id": 0, "zona": "$_id.zona", "hora": "$_id.hora", "horas_saturadas": 1,
                          "carga_prom": {"$round": ["$carga_prom", 1]},
                          "intensidad_prom": {"$round": ["$intensidad_prom", 0]}}},
            {"$sort": {"zona": 1, "hora": 1}},
        ]))

    # ----------------------------------------------------------- dashboard
    def serie_temporal(self, variable, unidad="day", **f):
        """Evolución de una variable agregada por hora/día/semana/mes (gráfico de serie temporal)."""
        filtro = self._filtro(**f)
        filtro[f"mediciones.{variable}"] = {"$exists": True}
        return list(self.db.lecturas.aggregate([
            {"$match": filtro},
            {"$group": {"_id": {"$dateTrunc": {"date": "$ts", "unit": unidad, "timezone": TZ}},
                        "promedio": {"$avg": f"$mediciones.{variable}"},
                        "maximo": {"$max": f"$mediciones.{variable}"}}},
            {"$project": {"_id": 0, "periodo": "$_id", "promedio": {"$round": ["$promedio", 2]},
                          "maximo": {"$round": ["$maximo", 2]}}},
            {"$sort": {"periodo": 1}},
        ]))

    def distribucion_por_tipo(self, **f):
        """Gráfico de dona: lecturas por tipo de sensor."""
        return list(self.db.lecturas.aggregate([
            {"$match": self._filtro(**f)},
            {"$group": {"_id": "$tipo", "lecturas": {"$sum": 1}}},
            {"$project": {"_id": 0, "tipo": "$_id", "lecturas": 1}},
            {"$sort": {"lecturas": -1}},
        ]))

    def mapa_sensores(self, variable="no2", **f):
        """Promedio de una variable por sensor con sus coordenadas (mapa de puntos / calor)."""
        filtro = self._filtro(**f)
        filtro[f"mediciones.{variable}"] = {"$exists": True}
        return list(self.db.lecturas.aggregate([
            {"$match": filtro},
            {"$group": {"_id": "$sensor_id", "valor": {"$avg": f"$mediciones.{variable}"},
                        "zona": {"$first": "$ubicacion.zona"},
                        "lon": {"$first": {"$arrayElemAt": ["$ubicacion.geo.coordinates", 0]}},
                        "lat": {"$first": {"$arrayElemAt": ["$ubicacion.geo.coordinates", 1]}}}},
            {"$project": {"_id": 0, "sensor_id": "$_id", "zona": 1, "lat": 1, "lon": 1,
                          "valor": {"$round": ["$valor", 2]}}},
        ]))

    def consumo_por_hora(self, **f):
        """Perfil horario de consumo eléctrico (KPI 'consumo por hora'; dato sintético)."""
        return list(self.db.lecturas.aggregate([
            {"$match": self._filtro(tipo="energia", **f)},
            {"$group": {"_id": {"$hour": {"date": "$ts", "timezone": TZ}}, "kwh": {"$avg": "$mediciones.consumo_kwh"}}},
            {"$project": {"_id": 0, "hora": "$_id", "kwh": {"$round": ["$kwh", 1]}}},
            {"$sort": {"hora": 1}},
        ]))

    def kpis(self, **f):
        """KPIs del dashboard que salen de MongoDB (ambiente, movilidad, energía, IoT) en una pasada.
        Nota: la red de Madrid no mide CO2; se reporta CO (monóxido de carbono) y NO2."""
        filtro = self._filtro(**f)
        r = list(self.db.lecturas.aggregate([
            {"$match": filtro},
            {"$group": {
                "_id": None, "mediciones": {"$sum": 1}, "sensores": {"$addToSet": "$sensor_id"},
                "desde": {"$min": "$ts"}, "hasta": {"$max": "$ts"},
                "temperatura": {"$avg": "$mediciones.temperatura"},
                "pm25": {"$avg": "$mediciones.pm25"}, "no2": {"$avg": "$mediciones.no2"},
                "co": {"$avg": "$mediciones.co"}, "humedad": {"$avg": "$mediciones.humedad"},
                "intensidad": {"$avg": "$mediciones.intensidad"},
                "velocidad": {"$avg": "$mediciones.velocidad_prom"},
                "congestion": {"$avg": "$mediciones.indice_congestion"},
                "tiempo_km": {"$avg": "$mediciones.tiempo_km_min"},
                "consumo_total": {"$sum": {"$ifNull": ["$mediciones.consumo_kwh", 0]}},
                "consumo_pico": {"$max": "$mediciones.consumo_kwh"},
            }}], allowDiskUse=True))
        g = r[0] if r else {}
        filtro_al = self._filtro(campo_tipo="tipo_sensor", **f)
        alertas = self.db.alertas.count_documents(filtro_al)
        alertas_amb = self.db.alertas.count_documents(
            {**filtro_al, "tipo_sensor": filtro_al.get("tipo_sensor", {"$in": ["calidad_aire", "clima", "ruido"]})})
        inc = self._filtro(desde=f.get("desde"), hasta=f.get("hasta"))  # Calle 30: sin zona
        horas = ((g["hasta"] - g["desde"]).total_seconds() / 3600) if g.get("desde") else 0
        rnd = lambda v, n=2: round(v, n) if isinstance(v, (int, float)) else None
        return {
            "fuente": FUENTE,
            "iot_mediciones": g.get("mediciones", 0),
            "iot_sensores_reportando": len(g.get("sensores", [])),
            "iot_alertas": alertas,
            "iot_mediciones_por_hora": rnd(g.get("mediciones", 0) / horas, 1) if horas else None,
            "iot_mediciones_por_dia": rnd(g.get("mediciones", 0) / horas * 24, 0) if horas else None,
            "iot_lecturas_por_segundo": rnd(g.get("mediciones", 0) / (horas * 3600), 3) if horas else None,
            "amb_temperatura_prom": rnd(g.get("temperatura"), 1),
            "amb_pm25_prom": rnd(g.get("pm25"), 1),
            "amb_no2_prom": rnd(g.get("no2"), 1),
            "amb_co_prom_mg_m3": rnd(g.get("co")),
            "amb_humedad_prom": rnd(g.get("humedad"), 1),
            "amb_alertas": alertas_amb,
            "mov_intensidad_prom_veh_h": rnd(g.get("intensidad"), 0),
            "mov_velocidad_prom_c30": rnd(g.get("velocidad"), 1),
            "mov_congestion_prom": rnd(g.get("congestion"), 3),
            "mov_tiempo_km_min_c30": rnd(g.get("tiempo_km")),
            "mov_accidentes_c30": self.db.incidentes.count_documents(inc),
            "ene_consumo_total_kwh": rnd(g.get("consumo_total"), 0),
            "ene_pico_kwh": rnd(g.get("consumo_pico"), 1),
        }


if __name__ == "__main__":
    q = ConsultasMongo()
    dic = dict(desde=datetime(2025, 12, 1), hasta=datetime(2026, 1, 1))
    pruebas = [
        ("zonas", lambda: q.zonas()),
        ("variables(calidad_aire)", lambda: q.variables("calidad_aire")),
        ("q1_lecturas_sensor", lambda: q.q1_lecturas_sensor("AIR-008", limite=20)),
        ("q2_lecturas_rango", lambda: q.q2_lecturas_rango(datetime(2025, 12, 1), datetime(2025, 12, 2), zona="Centro")),
        ("q3_max_min (dic-2025)", lambda: q.q3_max_min(**dic)),
        ("q4_eventos_criticos", lambda: q.q4_eventos_criticos(**dic)),
        ("q4_eventos_por_tipo", lambda: q.q4_eventos_por_tipo()),
        ("q5_alertas_por_zona", lambda: q.q5_alertas_por_zona(**dic)),
        ("q5_alertas_cercanas", lambda: q.q5_alertas_cercanas(nivel="critica")),
        ("q6_promedio_por_zona", lambda: q.q6_promedio_por_zona(**dic)),
        ("q7_promedio_por_tipo (dic-2025)", lambda: q.q7_promedio_por_tipo(**dic)),
        ("q8_incidentes_por_categoria", lambda: q.q8_incidentes_por_categoria()),
        ("q9_evolucion_mensual", lambda: q.q9_evolucion_mensual("no2", zona=["Salamanca", "Retiro"])),
        ("q10_saturacion_hora_pico", lambda: q.q10_saturacion_hora_pico(desde=datetime(2025, 1, 1))),
        ("serie_temporal", lambda: q.serie_temporal("temperatura", "day", **dic)),
        ("distribucion_por_tipo", lambda: q.distribucion_por_tipo(**dic)),
        ("mapa_sensores", lambda: q.mapa_sensores("no2", **dic)),
        ("consumo_por_hora", lambda: q.consumo_por_hora(**dic)),
    ]
    for nombre, fn in pruebas:
        t = time.time()
        res = fn()
        print(f"{nombre:<34} {len(res):>5} filas  {1000 * (time.time() - t):>7.0f} ms   {str(res[0])[:90] if res else ''}")
    print("\nKPIs dic-2025:")
    for k, v in q.kpis(**dic).items():
        print(f"  {k:<30} {v}")
