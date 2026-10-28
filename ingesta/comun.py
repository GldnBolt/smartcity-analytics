"""Utilidades para leer los datos crudos de Madrid; las usan el cargador de PostgreSQL y el importador de MongoDB."""
import csv
import io
import math
import os
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

RAW = Path(os.getenv("DIR_RAW", "data/raw/madrid"))
FECHA_INICIO = date.fromisoformat(os.getenv("FECHA_INICIO", "2024-01-01"))
FECHA_FIN = date.fromisoformat(os.getenv("FECHA_FIN", "2025-12-31"))
TZ_MADRID = ZoneInfo("Europe/Madrid")

# Los 21 distritos municipales de Madrid (código oficial -> nombre)
DISTRITOS = {
    1: "Centro", 2: "Arganzuela", 3: "Retiro", 4: "Salamanca", 5: "Chamartín", 6: "Tetuán",
    7: "Chamberí", 8: "Fuencarral-El Pardo", 9: "Moncloa-Aravaca", 10: "Latina", 11: "Carabanchel",
    12: "Usera", 13: "Puente de Vallecas", 14: "Moratalaz", 15: "Ciudad Lineal", 16: "Hortaleza",
    17: "Villaverde", 18: "Villa de Vallecas", 19: "Vicálvaro", 20: "San Blas-Canillejas", 21: "Barajas",
}
# Distritos dentro de la M-30 ("almendra central"); el resto es periferia
ALMENDRA = {1, 2, 3, 4, 5, 6, 7}


def id_zona(cod_distrito: int) -> str:
    return f"D{int(cod_distrito):02d}"


def area(cod_distrito: int) -> str:
    return "Almendra central" if int(cod_distrito) in ALMENDRA else "Periferia"


def leer_csv(ruta, sep=";"):
    """Lee un CSV del portal (algunos vienen en UTF-8 con BOM, otros en Latin-1).
    Normaliza encabezados con saltos de línea ("NUM\\nPLAZAS" -> "NUM PLAZAS") y descarta las líneas
    en blanco que algunos archivos (p. ej. paradas de la EMT) traen antes del encabezado."""
    datos = Path(ruta).read_bytes() if not isinstance(ruta, bytes) else ruta
    try:
        texto = datos.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = datos.decode("latin-1")
    texto = texto.lstrip("\r\n")
    lector = csv.DictReader(io.StringIO(texto), delimiter=sep)
    lector.fieldnames = [re.sub(r"\s+", " ", c).strip() for c in lector.fieldnames]
    return [{k: (v or "").strip() for k, v in fila.items() if k} for fila in lector]


def numero(s):
    """'12,5' / '12.5' / '38,3%' -> float; vacío, 'N/D' o no numérico -> None."""
    if s is None:
        return None
    s = str(s).strip().replace("%", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def coord_con_miles(s, digitos_enteros):
    """Las estaciones de ruido publican coordenadas como '-3.691.877' / '40.422.599'."""
    neg = s.strip().startswith("-")
    dig = re.sub(r"\D", "", s)
    if not dig:
        return None
    v = int(dig) / 10 ** (len(dig) - digitos_enteros)
    return -v if neg else v


def fecha_dmy(s):
    """'01/12/1998' -> '1998-12-01' (o '' si no se puede interpretar)."""
    try:
        return datetime.strptime(s.strip()[:10], "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


@lru_cache(maxsize=200_000)
def local_a_utc(anio, mes, dia, hora=0, minuto=0):
    """Hora local de Madrid (con horario de verano) -> datetime UTC naive para MongoDB."""
    local = datetime(anio, mes, dia, hora, minuto, tzinfo=TZ_MADRID)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def en_rango(anio, mes, dia):
    return FECHA_INICIO <= date(anio, mes, dia) <= FECHA_FIN


def distancia_m(lat1, lon1, lat2, lon2):
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
