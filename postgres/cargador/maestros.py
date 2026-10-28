"""
Extracción de los datos maestros que salen de archivos oficiales de Madrid.

  barrios   131 barrios del geoportal municipal (TopoJSON); los distritos se derivan en SQL
  sensores  estaciones y puntos reales; cada uno se ubica en su barrio por geometría

Los catálogos que define el proyecto (categorías, tipos, variables, umbrales y organismos) están en
postgres/sql/02_carga.sql. La `red` de cada sensor indica allí su organismo responsable.

Sensores:
  calidad_aire     24 estaciones de la red municipal de calidad del aire
  clima            26 estaciones meteorológicas municipales
  ruido            31 estaciones de la red de vigilancia acústica
  trafico          puntos de medida seleccionados: los 2 más cercanos a cada estación de aire (para
                   correlacionar tráfico y contaminación), al menos 2 por distrito y 8 de Calle 30
                   (los únicos con velocidad). El histórico completo de ~5 000 puntos es para HDFS.
  estacionamiento  aparcamientos públicos rotacionales con ocupación histórica
  energia          1 medidor SINTÉTICO por distrito (no hay dato abierto horario)
"""
from collections import defaultdict
from dataclasses import dataclass

from ingesta.comun import RAW, coord_con_miles, distancia_m, fecha_dmy, id_zona, leer_csv, numero
from ingesta.territorio import barrio_de, barrios

N_TRAFICO_POR_ESTACION = 2
N_TRAFICO_MIN_DISTRITO = 2
N_TRAFICO_M30 = 8
TOLERANCIA_M = 1500  # punto en el límite municipal: barrio de centroide más cercano dentro de 1,5 km


@dataclass
class Sensor:
    codigo: str
    nombre: str
    tipo_sensor: str
    red: str
    lat: float
    lon: float
    cod_barrio: str
    fecha_instalacion: str | None = None
    estado: str = "activo"
    fuente_datos: str = "real"

    def fila(self):
        """En el orden de las columnas de staging.sensores."""
        return (self.codigo, self.nombre, self.tipo_sensor, self.red, self.fecha_instalacion, self.estado,
                self.cod_barrio, self.lat, self.lon, self.fuente_datos)


def puntos_trafico():
    pts = [p for p in leer_csv(RAW / "trafico/puntos_trafico_2025.csv")
           if p["tipo_elem"] in ("URB", "M30") and p["distrito"] and numero(p["latitud"])]
    for p in pts:
        p["lat"], p["lon"], p["cod_distrito"] = float(p["latitud"]), float(p["longitud"]), int(p["distrito"])
    return pts


def mas_cercanos(lat, lon, candidatos, n):
    return sorted(candidatos, key=lambda p: distancia_m(lat, lon, p["lat"], p["lon"]))[:n]


def territorio():
    """-> [(codigo, nombre, cod_distrito, distrito, lat, lon)]  un barrio por fila (centroide)"""
    return [(b.codigo, b.nombre, b.cod_distrito, b.distrito, round(b.lat, 6), round(b.lon, 6))
            for b in sorted(barrios(), key=lambda b: int(b.codigo))]


def sensores():
    pts = puntos_trafico()
    res, omitidos = [], []

    def agregar(codigo, nombre, tipo, red, lat, lon, **kw):
        b = barrio_de(lat, lon, TOLERANCIA_M)
        if b is None:
            omitidos.append(codigo)  # fuera del término municipal: no se le puede asignar zona real
            return
        res.append(Sensor(codigo, nombre.strip()[:100], tipo, red, round(lat, 6), round(lon, 6),
                          b.codigo, **kw))

    aire = leer_csv(RAW / "aire/estaciones_aire.csv")
    for e in aire:
        agregar(f"AIR-{int(e['CODIGO_CORTO']):03d}", e["ESTACION"], "calidad_aire", "aire",
                float(e["LATITUD"]), float(e["LONGITUD"]), fecha_instalacion=fecha_dmy(e["Fecha alta"]) or None)

    for e in leer_csv(RAW / "meteo/estaciones_meteo.csv"):
        agregar(f"MET-{int(e['CÓDIGO_CORTO']):03d}", e["ESTACION"], "clima", "meteo",
                float(e["LATITUD"]), float(e["LONGITUD"]))

    for e in leer_csv(RAW / "ruido/estaciones_ruido.csv"):
        num = int(e["ESTACIÓN"].split("-")[1])
        agregar(f"RUI-{num:02d}", e["NOMBRE"], "ruido", "ruido",
                coord_con_miles(e["LATITUD"], 2), coord_con_miles(e["LONGITUD"], 1),
                fecha_instalacion=fecha_dmy(e["FECHA ALTA"]) or None)

    urb = [p for p in pts if p["tipo_elem"] == "URB"]
    m30 = [p for p in pts if p["tipo_elem"] == "M30"]
    elegidos = {}
    for e in aire:
        for p in mas_cercanos(float(e["LATITUD"]), float(e["LONGITUD"]), urb, N_TRAFICO_POR_ESTACION):
            elegidos[p["id"]] = p
    por_distrito = defaultdict(list)
    for p in urb:
        por_distrito[p["cod_distrito"]].append(p)
    for d, cand in por_distrito.items():
        la = sum(p["lat"] for p in cand) / len(cand)
        lo = sum(p["lon"] for p in cand) / len(cand)
        faltan = N_TRAFICO_MIN_DISTRITO - sum(1 for p in elegidos.values() if p["cod_distrito"] == d)
        for p in mas_cercanos(la, lo, [p for p in cand if p["id"] not in elegidos], max(0, faltan)):
            elegidos[p["id"]] = p
    m30.sort(key=lambda p: min(distancia_m(p["lat"], p["lon"], float(e["LATITUD"]), float(e["LONGITUD"]))
                               for e in aire))
    for p in m30[:N_TRAFICO_M30]:
        elegidos[p["id"]] = p
    for p in elegidos.values():
        agregar(f"TRF-{p['id']}", p["nombre"], "trafico", "calle30" if p["tipo_elem"] == "M30" else "movilidad",
                p["lat"], p["lon"])

    # Aparcamientos: activos si siguen reportando ocupación en 2025 (estado administrativo de la fuente)
    plazas = {r["CÓDIGO APARC"]: r for r in leer_csv(RAW / "parking/aparcamientos_plazas.csv")}
    por_nombre = {r["NOMBRE APARC"].upper(): r for r in plazas.values()}
    vistos, en_2025 = {}, set()
    for anio in ("2024", "2025"):
        for r in leer_csv(RAW / f"parking/ocupacion_parking_{anio}.csv"):
            if r["CÓDIDO PAR"] and r["CÓDIGO DISTRITO"].isdigit():  # descarta filas de totales
                vistos[r["CÓDIDO PAR"]] = r
                if anio == "2025":
                    en_2025.add(r["CÓDIDO PAR"])
    for cod, r in vistos.items():
        ref = plazas.get(cod) or por_nombre.get(r["NOMBRE DEL APARCAMIENTO"].upper())
        if not (ref and numero(ref["LATITUD"])):
            omitidos.append(f"PRK-{cod}")  # sin coordenadas publicadas: no se inventa una posición
            continue
        agregar(f"PRK-{cod}", r["NOMBRE DEL APARCAMIENTO"].title(), "estacionamiento", "parking",
                float(ref["LATITUD"]), float(ref["LONGITUD"]),
                estado="activo" if cod in en_2025 else "inactivo")

    # Energía sintética: un medidor agregado por distrito, en el barrio más cercano al centro del distrito
    bs = barrios()
    for d in sorted({b.cod_distrito for b in bs}):
        propios = [b for b in bs if b.cod_distrito == d]
        la = sum(b.lat for b in propios) / len(propios)
        lo = sum(b.lon for b in propios) / len(propios)
        b = next((b for b in propios if b.contiene(la, lo)), None) or \
            min(propios, key=lambda b: distancia_m(la, lo, b.lat, b.lon))
        res.append(Sensor(f"ENE-{id_zona(d)}", f"Medidor agregado {b.distrito}", "energia", "simulacion",
                          round(b.lat, 6), round(b.lon, 6), b.codigo, fuente_datos="sintetico"))
    return res, omitidos
