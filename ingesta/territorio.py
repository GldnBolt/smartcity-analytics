"""
Territorio oficial de Madrid: los 131 barrios municipales (TopoJSON del geoportal, WGS84).

- `barrio_de(lat, lon)` devuelve el barrio que contiene el punto (y con él su distrito).
- `utm30n_a_latlon(x, y)` convierte coordenadas ETRS89 / UTM 30N (las que usan varias fuentes
  del Ayuntamiento y de la EMT) a latitud/longitud.

Sin dependencias externas: el TopoJSON se decodifica y el punto-en-polígono se resuelve con
el algoritmo de cruce de rayos.
"""
import json
import math
from functools import lru_cache

from .comun import RAW, distancia_m


class Barrio:
    def __init__(self, props, anillos):
        self.codigo = props["COD_BAR"]                 # "171"
        self.nombre = props["NOMBRE"]
        self.cod_distrito = int(props["CODDIS"])
        self.distrito = props["NOMDIS"]
        self.anillos = anillos                         # [exterior, huecos...], cada uno [(lon, lat), ...]
        xs = [p[0] for p in anillos[0]]
        ys = [p[1] for p in anillos[0]]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        self.lon, self.lat = _centroide(anillos[0])

    def contiene(self, lat, lon):
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            return False
        return _dentro(lon, lat, self.anillos[0]) and not any(_dentro(lon, lat, h) for h in self.anillos[1:])


def _dentro(x, y, anillo):
    dentro = False
    j = len(anillo) - 1
    for i in range(len(anillo)):
        xi, yi = anillo[i]
        xj, yj = anillo[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def _centroide(anillo):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(anillo, anillo[1:] + anillo[:1]):
        f = x0 * y1 - x1 * y0
        a += f
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    return cx / (3 * a), cy / (3 * a)


@lru_cache(maxsize=1)
def barrios():
    topo = json.loads((RAW / "territorio/barrios.topojson").read_text(encoding="utf-8"))
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcos = []
    for arco in topo["arcs"]:  # coordenadas cuantizadas y codificadas como diferencias
        x = y = 0
        puntos = []
        for dx, dy in arco:
            x += dx
            y += dy
            puntos.append((x * sx + tx, y * sy + ty))
        arcos.append(puntos)

    def anillo(indices):
        pts = []
        for i in indices:
            seg = arcos[i] if i >= 0 else arcos[~i][::-1]
            pts.extend(seg if not pts else seg[1:])
        return pts

    res = []
    for g in topo["objects"]["Barrios"]["geometries"]:
        poligonos = [g["arcs"]] if g["type"] == "Polygon" else g["arcs"]
        for p in poligonos:  # todos los barrios son Polygon; MultiPolygon se tomaría por partes
            res.append(Barrio(g["properties"], [anillo(r) for r in p]))
    return res


def barrio_de(lat, lon, tolerancia_m=0):
    """Barrio que contiene el punto. Con tolerancia, si el punto cae fuera de todos (p. ej. justo en
    el límite municipal) se usa el barrio de centroide más cercano dentro de esa distancia."""
    for b in barrios():
        if b.contiene(lat, lon):
            return b
    if tolerancia_m:
        b = min(barrios(), key=lambda b: distancia_m(lat, lon, b.lat, b.lon))
        if distancia_m(lat, lon, b.lat, b.lon) <= tolerancia_m:
            return b
    return None


def utm30n_a_latlon(x, y):
    """ETRS89 / UTM zona 30N -> (lat, lon) en grados (elipsoide GRS80)."""
    a, f, k0 = 6378137.0, 1 / 298.257222101, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x -= 500000.0
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu))
    s, c, t = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = a / math.sqrt(1 - e2 * s * s)
    t1, c1 = t * t, ep2 * c * c
    r1 = a * (1 - e2) / (1 - e2 * s * s) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * t / r1) * (d * d / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * ep2) * d ** 4 / 24
                                  + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * ep2 - 3 * c1 * c1) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6
           + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * ep2 + 24 * t1 * t1) * d ** 5 / 120) / c
    return math.degrees(lat), -3.0 + math.degrees(lon)
