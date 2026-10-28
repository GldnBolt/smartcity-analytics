"""
Extracción del transporte público de la EMT de Madrid: líneas, recorridos, paradas, flota y demanda.

Las filas se entregan tal como las publica la EMT (con su historial de versiones). La selección de la
versión vigente, la limpieza y la consolidación de la demanda las hace postgres/sql/02_carga.sql.
Aquí solo se resuelve lo que SQL no puede leer: los ZIP y la geometría de las paradas (coordenadas del
GTFS o, si la parada no está en él, conversión de las UTM, y barrio que la contiene).
"""
import csv
import io
import zipfile

from ingesta.comun import RAW, leer_csv
from ingesta.territorio import barrio_de, utm30n_a_latlon

T = RAW / "transporte"


def _gtfs(nombre):
    with zipfile.ZipFile(T / "emt_gtfs.zip") as z:
        return leer_csv(z.read(nombre), sep=",")


def lineas():
    """-> [(linea, fecha_ini, fecha_fin, origen, destino)]  todas las versiones publicadas"""
    return [(r["line"], r["dateIni"], r["dateEnd"], r["nameFrom"], r["nameTo"])
            for r in leer_csv(T / "emt_lineas.csv", sep=",")]


def gtfs_rutas():
    """-> [(route_id, nombre comercial)]"""
    return [(r["route_id"], r["route_long_name"]) for r in _gtfs("routes.txt")]


def recorridos():
    """-> [(linea, fecha_ini, fecha_fin, parada, sentido, secuencia)]  todas las versiones, en orden"""
    return [(r["line"], r["dateIni"], r["dateEnd"], r["parada"].strip(), int(r["sentido"]),
             int(r["secuencia"])) for r in leer_csv(T / "emt_paradas.csv", sep=",")]


def paradas():
    """-> [(codigo, nombre, lat, lon, cod_barrio|None, desde_utm)]  una fila por parada"""
    gtfs = {s["stop_id"]: s for s in _gtfs("stops.txt")}
    res = {}
    for r in leer_csv(T / "emt_paradas.csv", sep=","):
        cod = r["parada"].strip()
        if cod in res:
            continue
        g = gtfs.get(cod)
        if g:
            lat, lon, nombre = float(g["stop_lat"]), float(g["stop_lon"]), g["stop_name"]
        else:
            lat, lon = utm30n_a_latlon(float(r["posX"]), float(r["posY"]))
            nombre = r["descparada"]
        b = barrio_de(lat, lon)  # None: parada fuera del término municipal
        res[cod] = (cod, nombre.strip(), round(lat, 6), round(lon, 6), b.codigo if b else None, g is None)
    return list(res.values())


def flota():
    """-> [(vehiculo, fecha_ini, fecha_fin, marca, modelo, combustible, capacidad)]  todas las versiones"""
    return [(r["vehicleId"], r["dateIni"], r["dateEnd"], r["trade"], r["model"], r["fuelType"], r["capacity"])
            for r in leer_csv(T / "emt_flota.csv", sep=",")]


def demanda():
    """Filas (fecha, linea, franja, viajeros) de 2024 y 2025, más las del 31-dic-2023: su madrugada
    (franjas Mnn) cae en el 1-ene-2024."""
    with zipfile.ZipFile(T / "emt_demanda_linea_franja_2019_2025.zip") as z:
        for anio in ("2023", "2024", "2025"):
            texto = io.TextIOWrapper(z.open(f"{anio}_dem_linea_franja.csv"), encoding="utf-8-sig")
            lector = csv.reader(texto, delimiter=";")
            next(lector)
            for fecha, linea, franja, viajeros in lector:
                if anio == "2023" and fecha != "2023-12-31":
                    continue
                yield fecha, linea, franja, int(viajeros)
